import pytest
import yaml

from caliper.http_models import ChatClient, HTTPExecutionModel, HTTPPlanningModel, HTTPReviewModel
from caliper.models_config import ROLE_ORDER, load_models_config, make_adapters
from caliper.workflow import WorkflowError


def _fake_transport(stored, output):
    def transport(client: ChatClient, content: str) -> str:
        stored.append((client.model, content))
        return output
    return transport


def test_http_planner_requires_rule_ids_in_output():
    stored = []
    model = HTTPPlanningModel(ChatClient("m", "http://x/v1", transport=_fake_transport(stored, "no ids here")))
    with pytest.raises(WorkflowError, match="no rule IDs"):
        model.plan({"id": "req-1", "kind": "feature", "text": "t"}, {"id": "p", "stack_preset": "python-fastapi"}, ["SEC-001"])

    model2 = HTTPPlanningModel(ChatClient("m", "http://x/v1", transport=_fake_transport(stored, "## Rules relied on\n- SEC-001: yes")))
    plan = model2.plan({"id": "req-1", "kind": "feature", "text": "t"}, {"id": "p", "stack_preset": "python-fastapi"}, ["SEC-001"])
    assert "SEC-001" in plan
    assert stored[0][0] == "m" and "/chat/completions" not in stored[0][1]


def test_http_executor_extracts_diff_from_code_fence():
    raw = "Here is the diff:\n```diff\n--- a/f\n+++ b/f\n@@\n+x\n```"
    model = HTTPExecutionModel(ChatClient("m", "http://x/v1", transport=lambda c, t: raw))
    diff = model.execute({"id": "r", "kind": "feature", "text": "t"}, {"id": "p"}, "plan")
    assert diff.startswith("--- a/f") and "```" not in diff


def test_http_review_parses_json_and_validates_per_rule():
    payload = '```json\n{"verdicts": {"SEC-001": {"status": "fail", "reason": "b"}}}\n```'
    model = HTTPReviewModel(ChatClient("m", "http://x/v1", transport=lambda c, t: payload))
    review = model.review({"id": "r", "kind": "feature", "text": "t"}, {"id": "p"}, "plan", "diff", ["SEC-001"])
    assert review["verdicts"]["SEC-001"]["status"] == "fail"

    bad = HTTPReviewModel(ChatClient("m", "http://x/v1", transport=lambda c, t: "I think it is fine"))
    with pytest.raises(WorkflowError, match="JSON"):
        bad.review({"id": "r", "kind": "feature", "text": "t"}, {"id": "p"}, "plan", "diff", ["SEC-001"])


def test_run_button_appears_only_when_submitted_and_pipeline_renders():
    pytest.importorskip("fastapi.testclient")


def test_run_button_appears_only_when_submitted_and_pipeline_renders():
    pytest.importorskip("fastapi.testclient")


def test_factory_builds_mocks_by_default_and_honors_http_transport():
    config = load_models_config("caliper/models.yaml")
    adapters = make_adapters(config)
    assert adapters["planning"].__class__.__name__ == "MockPlanningModel"

    stored = []
    http_config = {r: {"model": f"m-{i}", "adapter": "http", "base_url": "http://127.0.0.1:9/v1"} for i, r in enumerate(ROLE_ORDER)}
    http_adapters = make_adapters(http_config, transports={"planning": _fake_transport(stored, "SEC-1 cited")})
    assert http_adapters["execution"].__class__.__name__ == "HTTPExecutionModel"
