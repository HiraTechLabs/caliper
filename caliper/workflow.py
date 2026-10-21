import json
from pathlib import Path

MAX_LOOPS = 3


class WorkflowError(Exception):
    pass


class MockDeployAdapter:
    """Stand-in for the SSH/docker deploy adapter. Records the deploy only."""

    def deploy(self, project: dict, env: str, req: dict) -> str:
        return f"[mock-deploy] {project['id']} -> {env} for {req['id']}"


def validate_review(review: dict) -> None:
    if not isinstance(review, dict) or "verdicts" not in review:
        raise WorkflowError("Review output is invalid: no 'verdicts' key.")
    verdicts = review["verdicts"]
    if not verdicts or any("status" not in v or "reason" not in v for v in verdicts.values()):
        raise WorkflowError("Review output is invalid: entry without status/reason.")
    if any(v["status"] not in ("pass", "fail") for v in verdicts.values()):
        raise WorkflowError("Review output is invalid: status must be pass or fail.")


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
        self.deploy = deploy or MockDeployAdapter()
        self.docs = docs

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
        project = self.registry.get_project(req["project_id"])
        project["project_docs"] = (
            self.docs.read_cached(req["project_id"]) if self.docs is not None else {}
        )
        dir = self.artifact_dir(req_id)
        dir.mkdir(parents=True, exist_ok=True)

        self.requests.update(req_id, status="planning")
        plan = self.planning.plan(req, project, rule_ids)
        (dir / "plan.md").write_text(plan, encoding="utf-8")

        self.requests.update(req_id, status="executing")
        diff = self.execution.execute(req, project, plan)

        while True:
            self.requests.update(req_id, status="reviewing")
            review = self.review.review(req, project, plan, diff, rule_ids)
            validate_review(review)
            (dir / "review.json").write_text(json.dumps(review, indent=2), encoding="utf-8")
            (dir / "diff.patch").write_text(diff, encoding="utf-8")

            loops = req["loop_count"]
            if failed_rules(review) and loops + 1 < MAX_LOOPS:
                req = self.requests.update(req_id, loop_count=loops + 1)
                continue
            if failed_rules(review):
                self.requests.update(req_id, status="failed")
                return self.requests.get(req_id)
            self.requests.update(req_id, status="awaiting_approval")
            return self.requests.get(req_id)

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
        req = self.requests.update(req_id, status="executing", review_note=note.strip())
        dir = self.artifact_dir(req_id)
        with (dir / "human-feedback.log").open("a", encoding="utf-8") as f:
            f.write(note.strip() + "\n")
        self.requests.update(req_id, status="reviewing")
        return self.requests.get(req_id)

    def approve_merge(self, req_id: str) -> dict:
        req = self._expect(req_id, "awaiting_approval")
        project = self.registry.get_project(req["project_id"])
        (self.artifact_dir(req_id) / "merged.marker").write_text("merged with human approval\n", encoding="utf-8")
        self.requests.update(req_id, status="merged")
        return self.deploy_dev(req_id)

    def deploy_dev(self, req_id: str) -> dict:
        req = self._expect(req_id, "merged", "deployed_dev")
        project = self.registry.get_project(req["project_id"])
        record = self.deploy.deploy(project, "dev", req)
        (self.artifact_dir(req_id) / "deploy-dev.log").write_text(record + "\n", encoding="utf-8")
        return self.requests.update(req_id, status="deployed_dev")

    def approve_prod(self, req_id: str) -> dict:
        req = self._expect(req_id, "deployed_dev")
        project = self.registry.get_project(req["project_id"])
        record = self.deploy.deploy(project, "prod", req)
        (self.artifact_dir(req_id) / "deploy-prod.log").write_text(record + "\n", encoding="utf-8")
        return self.requests.update(req_id, status="live")
