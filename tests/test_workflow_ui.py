import pytest
from fastapi.testclient import TestClient
from caliper.main import create_app
from tests.mock_models import MockExecutionModel, MockPlanningModel, MockReviewModel
from caliper.registry import ProjectRegistry
from caliper.state import RequestStore
from caliper.workflow import Workflow
from tests.support import complete_setup


class FailingReview(MockReviewModel):
    force_fail = True


def _make(tmp_path, review=None):
    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    complete_setup(registry, "shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    workflow = Workflow(
        requests, registry, tmp_path / "projects",
        planning=MockPlanningModel(), execution=MockExecutionModel(),
        review=review or MockReviewModel(),
    )
    return TestClient(create_app(registry=registry, requests=requests, workflow=workflow))


@pytest.fixture()
def client(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    complete_setup(registry, "shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    workflow = Workflow(
        requests, registry, tmp_path / "projects",
        planning=MockPlanningModel(), execution=MockExecutionModel(), review=MockReviewModel(),
    )
    return TestClient(create_app(registry=registry, requests=requests, workflow=workflow))


def test_run_button_appears_only_when_submitted_and_pipeline_renders(client):
    client.post("/projects/shop-api/requests/new", data={"kind": "feature", "text": "Add refund endpoint", "extra": ""})
    page = client.get("/requests/req-1")
    assert "Run workflow" in page.text
    assert "Plan" in page.text and "Review" in page.text and "Prod" in page.text

    run = client.post("/requests/req-1/run", follow_redirects=True)
    assert run.status_code == 200
    assert "Awaiting approval" in run.text
    assert "All 12 rules passed" in run.text
    assert "PASS SEC-001" in run.text
    assert "mock change for req-1" in run.text
    assert "Run workflow" not in run.text


def test_failed_review_shows_failed_rules_first_and_alert(tmp_path):
    client = _make(tmp_path, review=FailingReview())
    client.post("/projects/shop-api/requests/new", data={"kind": "bugfix", "text": "Fix rounding", "extra": ""})
    response = client.post("/requests/req-1/run", follow_redirects=True)
    assert response.status_code == 200
    assert "Failed" in response.text
    assert "3 times" in response.text
    assert response.text.index("FAIL") < response.text.index("passed - show")
