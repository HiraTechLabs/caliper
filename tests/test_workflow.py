import json

import pytest

from caliper.registry import ProjectRegistry
from caliper.state import RequestStore
from caliper.workflow import MAX_LOOPS, ReviewInvalid, Workflow, WorkflowError, validate_review
from tests.mock_models import MockExecutionModel, MockPlanningModel, MockReviewModel, RecordingDeployAdapter
from tests.support import approve_principles, attach_real_repo


class FailingReview(MockReviewModel):
    force_fail = True


class RecordingExecution(MockExecutionModel):
    """Records the feedback each Execution call receives and marks the revision.

    The revision line is added inside the mock hunk so the result is a valid
    unified diff that ``git apply`` accepts.
    """

    def __init__(self):
        self.feedback_calls = []

    def execute(self, req, project, plan, feedback=None):
        self.feedback_calls.append(feedback)
        revision = "v1" if not feedback else "v2"
        diff = super().execute(req, project, plan)
        diff = diff.replace("@@ -0,0 +1,3 @@", "@@ -0,0 +1,4 @@")
        return diff + f"+Revision: {revision}\n"


class OnceFailingReview(MockReviewModel):
    """Fails the first review, passes every later one."""

    def __init__(self):
        self.reviews = 0

    def review(self, req, project, plan, diff, rule_ids, tests=None):
        self.reviews += 1
        self.force_fail = self.reviews == 1
        return super().review(req, project, plan, diff, rule_ids, tests=tests)


@pytest.fixture()
def env(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    attach_real_repo(tmp_path, registry, "shop-api")
    approve_principles(registry, "shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    workflow = Workflow(
        requests, registry, tmp_path / "projects",
        planning=MockPlanningModel(), execution=MockExecutionModel(), review=MockReviewModel(),
    )
    return registry, requests, workflow


def test_run_passes_to_awaiting_approval_and_writes_artifacts(env):
    _, _, workflow = env
    req = workflow.requests.create("shop-api", "feature", "Add refund endpoint")

    result = workflow.run(req["id"])

    assert result["status"] == "awaiting_approval"
    dir = workflow.artifact_dir(req["id"])
    assert (dir / "plan.md").read_text().count("SEC-") > 0
    review = json.loads((dir / "review.json").read_text())
    assert all(v["status"] == "pass" for v in review["verdicts"].values())
    assert "+Implements: Add refund endpoint" in (dir / "diff.patch").read_text()


def test_run_attaches_the_project_docs_to_the_model_context(env):
    _, requests, workflow = env

    class CapturingPlanning(MockPlanningModel):
        def plan(self, req, project, rule_ids):
            self.project = project
            return super().plan(req, project, rule_ids)

    class FakeDocs:
        def read_cached(self, project_id):
            return {"graph_text": "nodes: []\n", "decisions": [{"name": "0001-x.md", "content": "# X\n"}]}

    planning = CapturingPlanning()
    workflow.planning = planning
    workflow.docs = FakeDocs()
    req = requests.create("shop-api", "feature", "Add refund endpoint")

    workflow.run(req["id"])

    assert planning.project["project_docs"]["graph_text"] == "nodes: []\n"


def test_review_validates_per_rule_output(env):
    ok = {"verdicts": {"SEC-001": {"status": "pass", "reason": "ok"}}}
    validate_review(ok, ["SEC-001"])
    with pytest.raises(WorkflowError, match="verdicts"):
        validate_review({"hello": {}}, ["SEC-001"])
    with pytest.raises(WorkflowError, match="pass or fail"):
        validate_review({"verdicts": {"SEC-001": {"status": "maybe", "reason": "x"}}}, ["SEC-001"])
    with pytest.raises(WorkflowError, match="status/reason"):
        validate_review({"verdicts": {"SEC-001": {"status": "pass"}}}, ["SEC-001"])


def test_review_must_cover_exactly_the_approved_rule_ids():
    two_rules = ["SEC-001", "SEC-002"]
    validate_review(
        {"verdicts": {rid: {"status": "pass", "reason": "ok"} for rid in two_rules}},
        two_rules,
    )

    missing = {"verdicts": {"SEC-001": {"status": "pass", "reason": "ok"}}}
    with pytest.raises(ReviewInvalid, match="SEC-002"):
        validate_review(missing, two_rules)

    unknown = {"verdicts": {
        "SEC-001": {"status": "pass", "reason": "ok"},
        "SEC-999": {"status": "pass", "reason": "ok"},
    }}
    with pytest.raises(ReviewInvalid, match="SEC-999"):
        validate_review(unknown, ["SEC-001"])


def test_an_incomplete_review_counts_as_a_failed_loop(env):
    registry, requests, _ = env

    class IncompleteReview(MockReviewModel):
        def review(self, req, project, plan, diff, rule_ids, tests=None):
            review = super().review(req, project, plan, diff, rule_ids, tests=tests)
            review["verdicts"].pop("SEC-002")
            return review

    execution = RecordingExecution()
    workflow = Workflow(
        requests, registry, env[2].artifacts_root,
        planning=MockPlanningModel(), execution=execution, review=IncompleteReview(),
    )
    req = requests.create("shop-api", "feature", "Add refund endpoint")

    result = workflow.run(req["id"])

    assert result["status"] == "failed"
    assert len(execution.feedback_calls) == MAX_LOOPS
    error = (workflow.artifact_dir(req["id"]) / "review-error.txt").read_text()
    assert "SEC-002" in error


def test_the_planner_must_cite_approved_rules(env):
    registry, requests, _ = env

    class EmptyPlan(MockPlanningModel):
        def plan(self, req, project, rule_ids):
            return "# Plan\n\nNo rules cited.\n"

    class UnknownRulePlan(MockPlanningModel):
        def plan(self, req, project, rule_ids):
            return "# Plan\n\n## Rules relied on\n- SEC-999: made up\n"

    for planning, message in ((EmptyPlan(), "no .*rule"), (UnknownRulePlan(), "not approved")):
        workflow = Workflow(
            requests, registry, env[2].artifacts_root,
            planning=planning, execution=MockExecutionModel(), review=MockReviewModel(),
        )
        req = requests.create("shop-api", "feature", "Add refund endpoint")
        with pytest.raises(WorkflowError, match=message):
            workflow.run(req["id"])


def test_failed_review_loops_then_stops_for_human(env):
    _, requests, workflow = env
    requests_list = requests
    req = requests_list.create("shop-api", "bugfix", "Fix rounding", extra="ValueError: precision")
    failing = Workflow(
        requests, env[0], env[2].artifacts_root,
        planning=workflow.planning, execution=workflow.execution, review=FailingReview(),
    )

    result = failing.run(req["id"])

    assert result["status"] == "failed"
    assert result["loop_count"] == MAX_LOOPS - 1


def test_a_failed_review_re_runs_execution_with_the_failed_rules(env):
    registry, requests, _ = env
    execution = RecordingExecution()
    workflow = Workflow(
        requests, registry, env[2].artifacts_root,
        planning=MockPlanningModel(), execution=execution, review=OnceFailingReview(),
    )
    req = requests.create("shop-api", "feature", "Add refund endpoint")

    result = workflow.run(req["id"])

    assert result["status"] == "awaiting_approval"
    assert len(execution.feedback_calls) == 2
    assert execution.feedback_calls[0] is None
    feedback = execution.feedback_calls[1]
    assert feedback["failed_rules"]["SEC-001"]["reason"].startswith("[mock] SEC-001 violated")
    assert "+Revision: v2" in (workflow.artifact_dir(req["id"]) / "diff.patch").read_text()


def test_request_changes_re_runs_execution_with_the_human_note(env):
    registry, requests, _ = env
    execution = RecordingExecution()
    workflow = Workflow(
        requests, registry, env[2].artifacts_root,
        planning=MockPlanningModel(), execution=execution, review=MockReviewModel(),
    )
    req = requests.create("shop-api", "feature", "Add refund endpoint")
    workflow.run(req["id"])
    assert execution.feedback_calls == [None]

    result = workflow.request_changes(req["id"], "Validate refund input first")

    assert result["status"] == "awaiting_approval"
    feedback = execution.feedback_calls[-1]
    assert feedback["human_note"] == "Validate refund input first"
    assert "+Revision: v2" in (workflow.artifact_dir(req["id"]) / "diff.patch").read_text()


def test_without_a_deploy_adapter_the_request_is_merged_but_deploy_is_blocked(env):
    registry, requests, _ = env

    workflow = Workflow(
        requests, registry, env[2].artifacts_root,
        planning=MockPlanningModel(), execution=MockExecutionModel(), review=MockReviewModel(),
    )
    req = requests.create("shop-api", "feature", "Add refund endpoint")
    workflow.run(req["id"])

    merged = workflow.approve_merge(req["id"])

    assert merged["status"] == "merged"
    assert merged["merge_commit"]
    merge_note = (workflow.artifact_dir(req["id"]) / "merge-commit.txt").read_text()
    assert merged["merge_commit"] in merge_note
    assert not (workflow.artifact_dir(req["id"]) / "merged.marker").exists()
    assert not workflow.workspace.worktree_path("shop-api", req["id"]).exists()
    assert not (workflow.artifact_dir(req["id"]) / "deploy-dev.log").exists()
    assert workflow.deploy_configured is False
    with pytest.raises(WorkflowError, match="Deployment is not configured"):
        workflow.deploy_dev(req["id"])
    with pytest.raises(WorkflowError, match="Deployment is not configured"):
        workflow.approve_prod(req["id"])


def test_a_configured_deploy_adapter_is_used(env):
    registry, requests, _ = env
    deploy = RecordingDeployAdapter()
    workflow = Workflow(
        requests, registry, env[2].artifacts_root,
        planning=MockPlanningModel(), execution=MockExecutionModel(), review=MockReviewModel(),
        deploy=deploy,
    )
    req = requests.create("shop-api", "feature", "Add refund endpoint")
    workflow.run(req["id"])

    dev = workflow.approve_merge(req["id"])
    live = workflow.approve_prod(req["id"])

    assert dev["status"] == "deployed_dev"
    assert live["status"] == "live"
    assert deploy.calls == [("shop-api", "dev", req["id"]), ("shop-api", "prod", req["id"])]


def test_the_runtime_has_no_mock_deploy_adapter():
    import caliper.workflow as workflow_module

    assert not hasattr(workflow_module, "MockDeployAdapter")


def test_run_only_from_submitted(env):
    _, requests, workflow = env
    req = requests.create("shop-api", "feature", "x")
    requests.update(req["id"], status="planning")
    with pytest.raises(WorkflowError, match="expected 'submitted'"):
        workflow.run(req["id"])


def test_unknown_request_raises(env):
    _, _, workflow = env
    with pytest.raises(WorkflowError, match="does not exist"):
        workflow.run("req-9")


def test_run_refuses_a_project_without_approved_principles(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    workflow = Workflow(
        requests, registry, tmp_path / "projects",
        planning=MockPlanningModel(), execution=MockExecutionModel(), review=MockReviewModel(),
    )
    req = requests.create("shop-api", "feature", "Add refund endpoint")

    with pytest.raises(WorkflowError, match="approved principle rules"):
        workflow.run(req["id"])
