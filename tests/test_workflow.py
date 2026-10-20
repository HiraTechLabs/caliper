import json
import pytest

from caliper.mock_models import MockReviewModel
from caliper.registry import ProjectRegistry
from caliper.state import RequestStore
from caliper.workflow import MAX_LOOPS, Workflow, WorkflowError, validate_review


class FailingReview(MockReviewModel):
    force_fail = True


@pytest.fixture()
def env(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_mode="create_new")
    requests = RequestStore(tmp_path / "state.sqlite3")
    workflow = Workflow(requests, registry, tmp_path / "projects")
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


def test_review_validates_per_rule_output(env):
    validate_review({"verdicts": {"SEC-001": {"status": "pass", "reason": "ok"}}})
    with pytest.raises(WorkflowError, match="verdicts"):
        validate_review({"hello": {}})
    with pytest.raises(WorkflowError, match="pass or fail"):
        validate_review({"verdicts": {"SEC-001": {"status": "maybe", "reason": "x"}}})
    with pytest.raises(WorkflowError, match="status/reason"):
        validate_review({"verdicts": {"SEC-001": {"status": "pass"}}})


def test_failed_review_loops_then_stops_for_human(env):
    _, requests, workflow = env
    requests_list = requests
    req = requests_list.create("shop-api", "bugfix", "Fix rounding", extra="ValueError: precision")
    failing = Workflow(requests, env[0], env[2].artifacts_root, review=FailingReview())

    result = failing.run(req["id"])

    assert result["status"] == "failed"
    assert result["loop_count"] == MAX_LOOPS - 1


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
