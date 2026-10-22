import pytest
from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.registry import ProjectRegistry
from caliper.state import RequestStore
from caliper.workflow import Workflow
from tests.mock_models import MockExecutionModel, MockPlanningModel, MockReviewModel, RecordingDeployAdapter
from tests.support import attach_real_repo, complete_setup


@pytest.fixture()
def setup(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    attach_real_repo(tmp_path, registry, "shop-api")
    complete_setup(registry, "shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    workflow = Workflow(
        requests, registry, tmp_path / "projects",
        planning=MockPlanningModel(), execution=MockExecutionModel(), review=MockReviewModel(),
        deploy=RecordingDeployAdapter(),
    )
    client = TestClient(create_app(registry=registry, requests=requests, workflow=workflow))
    return client, tmp_path, requests, workflow


def _make_awaiting(client):
    client.post("/projects/shop-api/requests/new", data={"kind": "feature", "text": "Add refund endpoint", "extra": ""})
    client.post("/requests/req-1/run", follow_redirects=True)


def test_approval_gate_buttons_and_full_path_to_live(setup):
    client, _, _, _ = setup
    _make_awaiting(client)

    page = client.get("/requests/req-1")
    assert "Approve &amp; merge" in page.text
    assert "Request changes" in page.text

    approve = client.post("/requests/req-1/approve", follow_redirects=True)
    assert approve.status_code == 200
    assert "Deploy to Prod" in approve.text

    final = client.post("/requests/req-1/prod", follow_redirects=True)
    assert final.status_code == 200
    assert "Merged and deployed to Prod. Nothing left to do." in final.text


def test_approval_performs_a_real_merge_and_push(setup):
    client, _, _, workflow = setup
    _make_awaiting(client)
    response = client.post("/requests/req-1/approve", follow_redirects=True)
    assert response.status_code == 200
    stored = workflow.requests.get("req-1")
    assert stored["status"] == "deployed_dev"
    assert stored["merge_commit"]
    assert (workflow.artifact_dir("req-1") / "merge-commit.txt").is_file()
    assert (workflow.artifact_dir("req-1") / "deploy-dev.log").is_file()


def test_request_changes_returns_to_execution_with_note(setup):
    client, _, requests, _ = setup
    _make_awaiting(client)

    response = client.post("/requests/req-1/changes", data={"note": "Validate refund input first"}, follow_redirects=True)
    assert response.status_code == 200
    assert "Awaiting approval" in response.text
    stored = requests.get("req-1")
    assert stored["status"] == "awaiting_approval"
    assert stored["review_note"] == "Validate refund input first"
    assert "Human feedback" in response.text


def test_request_changes_requires_note(setup):
    client, _, _, _ = setup
    _make_awaiting(client)
    response = client.post("/requests/req-1/changes", data={"note": "  "})
    assert response.status_code == 422
    assert "note is required" in response.text


def test_without_a_deploy_adapter_the_page_says_it_is_not_configured(tmp_path):
    from caliper.main import create_app

    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    attach_real_repo(tmp_path, registry, "shop-api")
    complete_setup(registry, "shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    workflow = Workflow(
        requests, registry, tmp_path / "projects",
        planning=MockPlanningModel(), execution=MockExecutionModel(), review=MockReviewModel(),
    )
    client = TestClient(create_app(registry=registry, requests=requests, workflow=workflow))
    _make_awaiting(client)

    response = client.post("/requests/req-1/approve", follow_redirects=True)

    assert response.status_code == 200
    assert "Deployment is not configured" in response.text
    assert "Deploy to Prod" not in response.text
    assert requests.get("req-1")["status"] == "merged"


def test_invalid_transitions_rejected_with_guidance(setup):
    client, _, _, _ = setup
    client.post("/projects/shop-api/requests/new", data={"kind": "feature", "text": "x", "extra": ""})
    response = client.post("/requests/req-1/approve")
    assert response.status_code == 422
    assert "expected" in response.text
    assert "Traceback" not in response.text
