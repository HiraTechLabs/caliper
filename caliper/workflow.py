import json
from pathlib import Path

from caliper.mock_models import MAX_LOOPS, MockExecutionModel, MockPlanningModel, MockReviewModel, WorkflowError


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
    def __init__(self, requests, registry, artifacts_root: Path, planning=None, execution=None, review=None):
        self.requests = requests
        self.registry = registry
        self.artifacts_root = Path(artifacts_root)
        self.planning = planning or MockPlanningModel()
        self.execution = execution or MockExecutionModel()
        self.review = review or MockReviewModel()

    def artifact_dir(self, req_id: str) -> Path:
        req = self.requests.get(req_id)
        if req is None:
            raise WorkflowError(f"Request '{req_id}' does not exist.")
        dir = self.artifacts_root / req["project_id"] / req_id
        return dir

    def run(self, req_id: str) -> dict:
        from caliper.principles import PRINCIPLES
        rule_ids = [rid for meta in PRINCIPLES.values() for _, rid, _ in meta["rules"]]

        req = self.requests.get(req_id)
        if req is None:
            raise WorkflowError(f"Request '{req_id}' does not exist.")
        if req["status"] != "submitted":
            raise WorkflowError(f"Request {req_id} is '{req['status']}', expected 'submitted'.")
        project = self.registry.get_project(req["project_id"])
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
