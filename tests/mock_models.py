from caliper.workflow import Workflow


class MockPlanningModel:
    def plan(self, req: dict, project: dict, rule_ids: list[str]) -> str:
        cited = ", ".join(rule_ids)
        return (
            f"# Plan for {req['id']}\n\n"
            f"Request: {req['text']}\n\n"
            f"## Steps\n1. Locate the affected module ({project['id']}).\n"
            f"2. Implement the change described above.\n"
            f"3. Add tests covering the new behavior.\n4. Update decisions and graph.\n\n"
            f"## Rules relied on\n{cited}\n"
        )


class MockExecutionModel:
    def execute(self, req: dict, project: dict, plan: str, feedback: dict | None = None) -> str:
        file_name = "feature_refund.md" if req["kind"] == "feature" else "fix_rounding.md"
        return (
            f"--- /dev/null\n+++ b/{file_name}\n@@ -0,0 +1,3 @@\n"
            f"+[mock change for {req['id']}]\n"
            f"+Implements: {req['text']}\n"
            f"+Git source of truth: projects/{project['id']}\n"
        )


class MockReviewModel:
    def review(self, req: dict, project: dict, plan: str, diff: str, rule_ids: list[str],
               tests: str | None = None) -> dict:
        fail = bool(getattr(self, "force_fail", False))
        verdicts = {}
        for rid in rule_ids:
            if fail and rid.endswith("-001"):
                verdicts[rid] = {"status": "fail", "reason": f"[mock] {rid} violated by {req['id']}"}
            else:
                verdicts[rid] = {"status": "pass", "reason": "[mock] satisfied"}
        return {"verdicts": verdicts}


class RecordingDeployAdapter:
    """Test double for a real deploy adapter; lives under tests/, never in the app."""

    def __init__(self):
        self.calls = []

    def deploy(self, project: dict, env: str, req: dict) -> str:
        self.calls.append((project["id"], env, req["id"]))
        return f"[test-deploy] {project['id']} -> {env} for {req['id']}"


def mock_workflow(requests, registry, artifacts_root, review=None, deploy=None):
    return Workflow(
        requests,
        registry,
        artifacts_root,
        planning=MockPlanningModel(),
        execution=MockExecutionModel(),
        review=review or MockReviewModel(),
        deploy=deploy,
    )
