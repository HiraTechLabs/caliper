import pytest

from caliper.http_models import ChatClient, HTTPExecutionModel, HTTPPlanningModel, HTTPReviewModel
from caliper.models_config import ROLE_ORDER, make_http_adapters
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
        model.plan({"id": "req-1", "kind": "feature", "text": "t"}, {"id": "p"}, ["SEC-001"])

    model2 = HTTPPlanningModel(ChatClient("m", "http://x/v1", transport=_fake_transport(stored, "## Rules relied on\n- SEC-001: yes")))
    plan = model2.plan({"id": "req-1", "kind": "feature", "text": "t"}, {"id": "p"}, ["SEC-001"])
    assert "SEC-001" in plan
    assert stored[0][0] == "m"


def test_http_executor_extracts_diff_from_code_fence():
    raw = "Here is the diff:\n```diff\n--- a/f\n+++ b/f\n@@\n+x\n```"
    model = HTTPExecutionModel(ChatClient("m", "http://x/v1", transport=lambda c, t: raw))
    diff = model.execute({"id": "r", "kind": "feature", "text": "t"}, {"id": "p"}, "plan")
    assert diff.startswith("--- a/f") and "```" not in diff


def test_http_executor_puts_failed_rules_and_the_human_note_in_the_prompt():
    stored = []
    model = HTTPExecutionModel(ChatClient(
        "m", "http://x/v1", transport=_fake_transport(stored, "--- a/f\n+++ b/f\n@@\n+x"),
    ))

    model.execute(
        {"id": "r", "kind": "feature", "text": "t"}, {"id": "p"}, "plan",
        feedback={
            "failed_rules": {"SEC-001": {"status": "fail", "reason": "hardcoded password"}},
            "human_note": "Validate the refund input first",
        },
    )

    prompt = stored[0][1]
    assert "SEC-001" in prompt and "hardcoded password" in prompt
    assert "Validate the refund input first" in prompt
    assert "NEW" in prompt


def test_http_review_parses_json_and_validates_per_rule():
    payload = '```json\n{"verdicts": {"SEC-001": {"status": "fail", "reason": "b"}}}\n```'
    model = HTTPReviewModel(ChatClient("m", "http://x/v1", transport=lambda c, t: payload))
    review = model.review({"id": "r", "kind": "feature", "text": "t"}, {"id": "p"}, "plan", "diff", ["SEC-001"])
    assert review["verdicts"]["SEC-001"]["status"] == "fail"

    bad = HTTPReviewModel(ChatClient("m", "http://x/v1", transport=lambda c, t: "I think it is fine"))
    with pytest.raises(WorkflowError, match="JSON"):
        bad.review({"id": "r", "kind": "feature", "text": "t"}, {"id": "p"}, "plan", "diff", ["SEC-001"])


def test_prompts_include_the_project_graph_and_decisions():
    stored = []
    project = {
        "id": "p",
        "project_docs": {
            "graph_text": "nodes:\n  - id: api\n    path: src/api.py\nedges: []\n",
            "decisions": [{"name": "0001-queue.md", "content": "# Use a queue\n"}],
        },
    }
    req = {"id": "r", "kind": "feature", "text": "t"}
    planner = HTTPPlanningModel(ChatClient(
        "m", "http://x/v1", transport=_fake_transport(stored, "## Rules relied on\n- SEC-001: yes")
    ))
    planner.plan(req, project, ["SEC-001"])
    executor = HTTPExecutionModel(ChatClient(
        "m", "http://x/v1", transport=_fake_transport(stored, "--- a/f\n+++ b/f\n@@\n+x")
    ))
    executor.execute(req, project, "plan")
    reviewer = HTTPReviewModel(ChatClient(
        "m", "http://x/v1",
        transport=_fake_transport(stored, '{"verdicts": {"SEC-001": {"status": "pass", "reason": "ok"}}}'),
    ))
    reviewer.review(req, project, "plan", "diff", ["SEC-001"])

    for _, prompt in stored:
        assert "Project graph:" in prompt
        assert "id: api" in prompt
        assert "Project decisions:" in prompt
        assert "Use a queue" in prompt


def test_factory_builds_http_adapters_and_honors_injected_transport():
    config = {
        role: {"base_url": f"http://127.0.0.1:{9000 + index}/v1", "model": f"m-{index}"}
        for index, role in enumerate(ROLE_ORDER)
    }
    stored = []
    adapters = make_http_adapters(
        config,
        transports={"planning": _fake_transport(stored, "SEC-1 cited")},
    )
    assert adapters["planning"].__class__.__name__ == "HTTPPlanningModel"
    assert adapters["execution"].__class__.__name__ == "HTTPExecutionModel"
    assert adapters["review"].__class__.__name__ == "HTTPReviewModel"
