import pytest
from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.registry import ProjectRegistry
from caliper.state import RequestStore
from caliper.workflow import Workflow
from tests.mock_models import MockExecutionModel, MockPlanningModel, MockReviewModel
from tests.support import complete_setup


@pytest.fixture()
def setup(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    complete_setup(registry, "shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    workflow = Workflow(
        requests, registry, tmp_path / "projects",
        planning=MockPlanningModel(), execution=MockExecutionModel(), review=MockReviewModel(),
    )
    client = TestClient(create_app(registry=registry, requests=requests, workflow=workflow))
    return client, requests, workflow


def _artifacts(workflow, req_id):
    directory = workflow.artifact_dir(req_id)
    directory.mkdir(parents=True)
    (directory / "plan.md").write_text("# plan\n", encoding="utf-8")
    (directory / "diff.patch").write_text("--- a\n+++ b\n", encoding="utf-8")
    return directory


def test_api_deletes_a_finished_request_and_its_artifacts(setup):
    client, requests, workflow = setup
    req = requests.create("shop-api", "feature", "Add refunds")
    requests.update(req["id"], status="failed")
    directory = _artifacts(workflow, req["id"])

    response = client.delete(f"/api/requests/{req['id']}")

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "id": req["id"], "project_id": "shop-api"}
    assert requests.get(req["id"]) is None
    assert not directory.exists()
    assert client.delete(f"/api/requests/{req['id']}").status_code == 404


def test_api_refuses_to_delete_an_in_flight_request(setup):
    client, requests, workflow = setup
    req = requests.create("shop-api", "feature", "Add refunds")
    requests.update(req["id"], status="executing")
    directory = _artifacts(workflow, req["id"])

    response = client.delete(f"/api/requests/{req['id']}")

    assert response.status_code == 409
    assert "executing" in response.json()["error"]
    assert requests.get(req["id"]) is not None
    assert directory.is_dir()


def test_form_delete_redirects_to_project_and_removes_the_row(setup):
    client, requests, workflow = setup
    req = requests.create("shop-api", "bugfix", "Fix refunds")
    requests.update(req["id"], status="failed")
    _artifacts(workflow, req["id"])

    response = client.post(f"/requests/{req['id']}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/projects/shop-api#requests"
    assert requests.get(req["id"]) is None
    assert client.get(f"/requests/{req['id']}").status_code == 404


def test_request_page_has_a_delete_confirmation(setup):
    client, requests, _ = setup
    req = requests.create("shop-api", "feature", "Add refunds")

    page = client.get(f"/requests/{req['id']}")

    assert page.status_code == 200
    assert "Danger zone" in page.text
    assert "Delete request" in page.text
    assert f'action="/requests/{req["id"]}/delete"' in page.text
