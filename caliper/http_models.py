import json

from caliper.workflow import ReviewInvalid, WorkflowError, validate_plan, validate_review


class ChatClient:
    """One HTTP chat client against an OpenAI-compatible /chat/completions endpoint.

    `transport` is injectable for tests; real transport is httpx.
    """

    def __init__(self, model: str, base_url: str, api_key: str | None = None, timeout: float = 600, transport=None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self._transport = transport

    def post(self, content: str) -> str:
        if self._transport is not None:
            return self._transport(self, content)
        import httpx
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.2,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected response shape from {self.base_url}: {exc}")


def _extract_code_block(text: str) -> str:
    lines = text.strip().splitlines()
    fence_starts = [i for i, line in enumerate(lines) if line.startswith("```")]
    if len(fence_starts) >= 2:
        return "\n".join(lines[fence_starts[0] + 1:fence_starts[-1]])
    return text


def _no_duplicate_keys(pairs):
    """Reject a repeated key: a duplicate rule verdict silently overwrites the first one."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReviewInvalid(f"Review output is invalid: duplicate rule id '{key}'.")
        result[key] = value
    return result


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _extract_code_block(stripped)
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1:
        raise ReviewInvalid("Review model did not return JSON. A review with no rule verdicts is invalid.")
    try:
        return json.loads(stripped[start:end + 1], object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ReviewInvalid(f"Review model returned unparseable JSON: {exc}")


def _docs_block(project: dict) -> str:
    docs = project.get("project_docs") or {}
    graph = docs.get("graph_text") or ""
    decisions = docs.get("decisions") or []
    parts = []
    if graph:
        parts.append("Project graph:\n" + graph)
    if decisions:
        parts.append(
            "Project decisions:\n"
            + "\n\n".join(f"### {item['name']}\n{item['content']}" for item in decisions[:20])
        )
    return ("\n" + "\n\n".join(parts) + "\n") if parts else ""


class HTTPPlanningModel:
    def __init__(self, client: ChatClient):
        self.client = client

    def plan(self, req: dict, project: dict, rule_ids: list[str]) -> str:
        prompt = (
            "You are the Planning Model of a private software factory.\n"
            f"Project: {project['id']}\n"
            + _docs_block(project)
            + f"Request type: {req['kind']}\nRequest text: {req['text']}\n"
            + (f"Error/log attached:\n{req['extra']}\n" if req.get("extra") else "")
            + "Write a short, concrete implementation plan in markdown.\n"
            "Include a section '## Rules relied on' listing every principle rule you rely on.\n"
            f"You may only use these rule IDs: {', '.join(rule_ids)}\n"
            "A plan without rule IDs is invalid."
        )
        output = self.client.post(prompt)
        if "SEC-" not in output and "ARC-" not in output and "PRF-" not in output and "CLC-" not in output and "BUS-" not in output and "TEC-" not in output:
            raise WorkflowError("Planner output contains no rule IDs. A plan without rule IDs is invalid.")
        validate_plan(output, rule_ids)
        return output


class HTTPExecutionModel:
    def __init__(self, client: ChatClient):
        self.client = client

    def execute(self, req: dict, project: dict, plan: str, feedback: dict | None = None) -> str:
        feedback = feedback or {}
        failed = feedback.get("failed_rules") or {}
        note = str(feedback.get("human_note") or "").strip()
        parts = [
            "You are the Execution Model. Produce code changes for the project on a branch.\n"
            f"Project: {project['id']}\n"
            + _docs_block(project)
            + f"Request: {req['text']}\nPlan:\n{plan}\n"
        ]
        code_context = str(project.get("code_context") or "").strip()
        if code_context:
            parts.append(
                "Current project code (the plan names the relevant files). "
                "Your diff must apply to this exact content:\n" + code_context + "\n\n"
            )
        if failed:
            parts.append(
                "The previous review failed these rules; fix every one of them:\n"
                + "\n".join(f"- {rid}: {v.get('reason', '')}" for rid, v in failed.items())
                + "\n"
            )
        if note:
            parts.append(f"The human asked for these changes:\n{note}\n")
        invalid = str(feedback.get("invalid_review") or "").strip()
        if invalid:
            parts.append(f"The previous review could not be used: {invalid}\n")
        patch_error = str(feedback.get("invalid_patch") or "").strip()
        if patch_error:
            parts.append(
                "The previous change could not be applied safely; return a new complete diff "
                f"that applies to the files above:\n{patch_error}\n"
            )
        test_failure = str(feedback.get("test_failure") or "").strip()
        if test_failure:
            parts.append(
                "The project's tests failed for the previous change. Fix the code so they pass:\n"
                + test_failure[-8000:] + "\n"
            )
        parts.append("Return ONLY a NEW complete unified diff (one or more files). No prose outside the diff.")
        return _extract_code_block(self.client.post("".join(parts)))


class HTTPReviewModel:
    def __init__(self, client: ChatClient):
        self.client = client

    def review(self, req: dict, project: dict, plan: str, diff: str, rule_ids: list[str],
               tests: str | None = None) -> dict:
        prompt = (
            "You are the Review Model. Judge the proposed change against each rule, one by one.\n"
            + _docs_block(project)
            + f"Request: {req['text']}\n\nPlan:\n{plan}\n\nDiff:\n{diff}\n\n"
            + (f"Test results from the project's test command:\n{tests}\n\n" if tests else "")
            + "Rules to judge: " + ", ".join(rule_ids) + "\n"
            "Return exactly one verdict for every rule listed above and no other rule id.\n"
            "Reply with ONLY a JSON object: {\"verdicts\": {\"<rule-id>\": {\"status\": \"pass\"|\"fail\", \"reason\": \"...\"}}}."
        )
        parsed = _extract_json(self.client.post(prompt))
        validate_review(parsed, rule_ids)
        return parsed
