import json

from caliper.workflow import validate_review, WorkflowError


class ChatClient:
    """One HTTP chat client against an OpenAI-compatible /chat/completions endpoint.

    `transport` is injectable for tests; real transport is httpx.
    """

    def __init__(self, model: str, base_url: str, transport=None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    def post(self, content: str) -> str:
        if self._transport is not None:
            return self._transport(self, content)
        import httpx
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.2,
            },
            timeout=600,
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


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _extract_code_block(stripped)
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1:
        raise WorkflowError("Review model did not return JSON. A review with no rule verdicts is invalid.")
    try:
        return json.loads(stripped[start:end + 1])
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Review model returned unparseable JSON: {exc}")


class HTTPPlanningModel:
    def __init__(self, client: ChatClient):
        self.client = client

    def plan(self, req: dict, project: dict, rule_ids: list[str]) -> str:
        prompt = (
            "You are the Planning Model of a private software factory.\n"
            f"Project: {project['id']} (stack preset: {project['stack_preset']})\n"
            f"Request type: {req['kind']}\nRequest text: {req['text']}\n"
            + (f"Error/log attached:\n{req['extra']}\n" if req.get("extra") else "")
            + "Write a short, concrete implementation plan in markdown.\n"
            "Include a section '## Rules relied on' listing every principle rule you rely on.\n"
            f"You may only use these rule IDs: {', '.join(rule_ids)}\n"
            "A plan without rule IDs is invalid."
        )
        output = self.client.post(prompt)
        if "SEC-" not in output and "ARC-" not in output and "PRF-" not in output and "CLC-" not in output and "BUS-" not in output and "TEC-" not in output:
            raise WorkflowError("Planner output contains no rule IDs. A plan without rule IDs is invalid.")
        return output


class HTTPExecutionModel:
    def __init__(self, client: ChatClient):
        self.client = client

    def execute(self, req: dict, project: dict, plan: str) -> str:
        prompt = (
            "You are the Execution Model. Produce code changes for the project on a branch.\n"
            f"Project: {project['id']}\nRequest: {req['text']}\nPlan:\n{plan}\n"
            "Return ONLY a unified diff (one or more files). No prose outside the diff."
        )
        return _extract_code_block(self.client.post(prompt))


class HTTPReviewModel:
    def __init__(self, client: ChatClient):
        self.client = client

    def review(self, req: dict, project: dict, plan: str, diff: str, rule_ids: list[str]) -> dict:
        prompt = (
            "You are the Review Model. Judge the proposed change against each rule, one by one.\n"
            f"Request: {req['text']}\n\nPlan:\n{plan}\n\nDiff:\n{diff}\n\n"
            "Rules to judge: " + ", ".join(rule_ids) + "\n"
            "Reply with ONLY a JSON object: {\"verdicts\": {\"<rule-id>\": {\"status\": \"pass\"|\"fail\", \"reason\": \"...\"}}}."
        )
        parsed = _extract_json(self.client.post(prompt))
        validate_review(parsed)
        return parsed
