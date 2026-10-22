import json
import re
from pathlib import Path

MAX_LOOPS = 3

RULE_ID_RE = re.compile(r"\b([A-Z]{3}-\d{3})\b")


class WorkflowError(Exception):
    pass


class ReviewInvalid(WorkflowError):
    """The review output cannot be used: structure or rule coverage is wrong."""


def validate_review(review: dict, rule_ids: list[str]) -> None:
    if not isinstance(review, dict) or "verdicts" not in review:
        raise ReviewInvalid("Review output is invalid: no 'verdicts' key.")
    verdicts = review["verdicts"]
    if not verdicts or any("status" not in v or "reason" not in v for v in verdicts.values()):
        raise ReviewInvalid("Review output is invalid: entry without status/reason.")
    if any(v["status"] not in ("pass", "fail") for v in verdicts.values()):
        raise ReviewInvalid("Review output is invalid: status must be pass or fail.")
    expected = set(rule_ids)
    returned = set(verdicts)
    missing = sorted(expected - returned)
    if missing:
        raise ReviewInvalid(
            "Review output is invalid: no verdict for approved rule(s): " + ", ".join(missing) + "."
        )
    unknown = sorted(returned - expected)
    if unknown:
        raise ReviewInvalid(
            "Review output is invalid: verdict for non-approved rule(s): " + ", ".join(unknown) + "."
        )


def cited_rule_ids(text: str) -> list[str]:
    """Rule IDs the planner cites, in first-citation order, without repeats."""
    return list(dict.fromkeys(RULE_ID_RE.findall(text or "")))


def validate_plan(plan: str, rule_ids: list[str]) -> None:
    cited = cited_rule_ids(plan)
    if not cited:
        raise WorkflowError(
            "Planning output cites no principle rule IDs. A plan without rule IDs is invalid."
        )
    approved = set(rule_ids)
    unknown = [rid for rid in cited if rid not in approved]
    if unknown:
        raise WorkflowError(
            "Planning output cites rules that are not approved: " + ", ".join(unknown) + "."
        )


class DeploymentNotConfigured(WorkflowError):
    """No real deploy adapter is configured; Caliper must not fake a deploy."""


def failed_rules(review: dict) -> list[str]:
    return [rid for rid, v in review["verdicts"].items() if v["status"] == "fail"]


class Workflow:
    def __init__(self, requests, registry, artifacts_root: Path, planning=None, execution=None,
                 review=None, deploy=None, docs=None):
        self.requests = requests
        self.registry = registry
        self.artifacts_root = Path(artifacts_root)
        self.planning = planning
        self.execution = execution
        self.review = review
        self.deploy = deploy
        self.docs = docs

    @property
    def deploy_configured(self) -> bool:
        return self.deploy is not None

    def missing_models(self) -> list[str]:
        labels = {"planning": "Planning", "execution": "Execution", "review": "Review"}
        return [labels[role] for role in ("planning", "execution", "review") if getattr(self, role) is None]

    @property
    def models_configured(self) -> bool:
        return not self.missing_models()

    def artifact_dir(self, req_id: str) -> Path:
        req = self.requests.get(req_id)
        if req is None:
            raise WorkflowError(f"Request '{req_id}' does not exist.")
        dir = self.artifacts_root / req["project_id"] / req_id
        return dir

    def _project_context(self, project_id: str) -> dict:
        project = self.registry.get_project(project_id)
        project["project_docs"] = (
            self.docs.read_cached(project_id) if self.docs is not None else {}
        )
        return project

    def _feedback(self, req: dict, review: dict | None) -> dict | None:
        """What Execution must address on a re-run: failed verdicts and the human note."""
        failed = {}
        if review is not None:
            failed = {rid: review["verdicts"][rid] for rid in failed_rules(review)}
        note = (req.get("review_note") or "").strip()
        if not failed and not note:
            return None
        return {"failed_rules": failed, "human_note": note}

    def _execute(self, req: dict, project: dict, plan: str, feedback: dict | None = None) -> str:
        return self.execution.execute(req, project, plan, feedback)

    def _review_until_done(self, req: dict, project: dict, plan: str, rule_ids: list[str], diff: str) -> dict:
        """Review the diff; on a failed or invalid review re-run Execution, up to MAX_LOOPS executions."""
        req_id = req["id"]
        dir = self.artifact_dir(req_id)
        while True:
            self.requests.update(req_id, status="reviewing")
            review = None
            error = None
            try:
                review = self.review.review(req, project, plan, diff, rule_ids)
                validate_review(review, rule_ids)
            except ReviewInvalid as exc:
                error = str(exc)

            (dir / "diff.patch").write_text(diff, encoding="utf-8")
            if error:
                (dir / "review-error.txt").write_text(error + "\n", encoding="utf-8")
            else:
                (dir / "review-error.txt").unlink(missing_ok=True)
                (dir / "review.json").write_text(json.dumps(review, indent=2), encoding="utf-8")

            loops = req["loop_count"]
            failed = failed_rules(review) if review is not None else []
            if (error or failed) and loops + 1 < MAX_LOOPS:
                req = self.requests.update(req_id, loop_count=loops + 1)
                self.requests.update(req_id, status="executing")
                feedback = self._feedback(req, review)
                if error:
                    feedback = feedback or {}
                    feedback["invalid_review"] = error
                diff = self._execute(req, project, plan, feedback)
                continue
            if error or failed:
                self.requests.update(req_id, status="failed")
                return self.requests.get(req_id)
            self.requests.update(req_id, status="awaiting_approval")
            return self.requests.get(req_id)

    def run(self, req_id: str) -> dict:
        missing = self.missing_models()
        if missing:
            raise WorkflowError("Models are not configured: " + ", ".join(missing) + ". Open Settings > Models.")
        req = self.requests.get(req_id)
        if req is None:
            raise WorkflowError(f"Request '{req_id}' does not exist.")
        if req["status"] != "submitted":
            raise WorkflowError(f"Request {req_id} is '{req['status']}', expected 'submitted'.")
        rule_ids = self.registry.approved_rule_ids(req["project_id"])
        if not rule_ids:
            raise WorkflowError(
                "The project has no approved principle rules. Approve the principles first."
            )
        project = self._project_context(req["project_id"])
        dir = self.artifact_dir(req_id)
        dir.mkdir(parents=True, exist_ok=True)

        self.requests.update(req_id, status="planning")
        plan = self.planning.plan(req, project, rule_ids)
        (dir / "plan.md").write_text(plan, encoding="utf-8")
        validate_plan(plan, rule_ids)

        self.requests.update(req_id, status="executing")
        diff = self._execute(req, project, plan)
        return self._review_until_done(req, project, plan, rule_ids, diff)

    def _expect(self, req_id: str, *statuses: str) -> dict:
        req = self.requests.get(req_id)
        if req is None:
            raise WorkflowError(f"Request '{req_id}' does not exist.")
        if req["status"] not in statuses:
            expected = " or ".join(statuses)
            raise WorkflowError(f"Request {req_id} is '{req['status']}', expected '{expected}'.")
        return req

    def request_changes(self, req_id: str, note: str) -> dict:
        req = self._expect(req_id, "awaiting_approval")
        if not note or not note.strip():
            raise WorkflowError("A note is required when requesting changes.")
        note = note.strip()
        req = self.requests.update(req_id, status="executing", review_note=note)
        dir = self.artifact_dir(req_id)
        with (dir / "human-feedback.log").open("a", encoding="utf-8") as f:
            f.write(note + "\n")
        plan = (dir / "plan.md").read_text(encoding="utf-8")
        project = self._project_context(req["project_id"])
        rule_ids = self.registry.approved_rule_ids(req["project_id"])
        diff = self._execute(req, project, plan, self._feedback(req, None))
        return self._review_until_done(req, project, plan, rule_ids, diff)

    def approve_merge(self, req_id: str) -> dict:
        req = self._expect(req_id, "awaiting_approval")
        (self.artifact_dir(req_id) / "merged.marker").write_text("merged with human approval\n", encoding="utf-8")
        self.requests.update(req_id, status="merged")
        if not self.deploy_configured:
            return self.requests.get(req_id)
        return self.deploy_dev(req_id)

    def _require_deploy(self) -> None:
        if not self.deploy_configured:
            raise DeploymentNotConfigured(
                "Deployment is not configured. The change is merged, but Dev and Prod deploys "
                "stay blocked until a deploy adapter is set up."
            )

    def deploy_dev(self, req_id: str) -> dict:
        self._require_deploy()
        req = self._expect(req_id, "merged", "deployed_dev")
        project = self.registry.get_project(req["project_id"])
        record = self.deploy.deploy(project, "dev", req)
        (self.artifact_dir(req_id) / "deploy-dev.log").write_text(record + "\n", encoding="utf-8")
        return self.requests.update(req_id, status="deployed_dev")

    def approve_prod(self, req_id: str) -> dict:
        self._require_deploy()
        req = self._expect(req_id, "deployed_dev")
        project = self.registry.get_project(req["project_id"])
        record = self.deploy.deploy(project, "prod", req)
        (self.artifact_dir(req_id) / "deploy-prod.log").write_text(record + "\n", encoding="utf-8")
        return self.requests.update(req_id, status="live")
