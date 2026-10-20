import pytest
from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.registry import ProjectRegistry
from caliper.state import RequestStore


@pytest.fixture()
def bare(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects")
    requests = RequestStore(tmp_path / "state.sqlite3")
    return TestClient(create_app(registry=registry, requests=requests)), registry, tmp_path


def test_empty_dashboard_shows_guidance_per_section(bare):
    client, _, _ = bare
    response = client.get("/")
    assert "All clear" in response.text
    assert "No request is being processed" in response.text
    assert "No requests yet" in response.text


def test_global_composer_requires_project_first(bare):
    client, _, _ = bare
    response = client.get("/requests/new")
    assert "cannot take a request" in response.text
    assert "New project" in response.text


def test_global_composer_with_project_shows_selector(bare):
    client, registry, _ = bare
    registry.create_project(name="Shop API", repo_mode="create_new")
    response = client.get("/requests/new")
    assert 'name="project_id"' in response.text
    assert "Shop API" in response.text


def test_global_composer_submit_creates_and_redirects(bare):
    client, registry, _ = bare
    registry.create_project(name="Shop API", repo_mode="create_new")
    response = client.post(
        "/requests/new",
        data={"project_id": "shop-api", "kind": "bugfix", "text": "Login redirect broken", "extra": ""},
        follow_redirects=True,
    )
    assert "req-1" in response.text
    assert "Run workflow" in response.text


def test_status_snapshot_reports_pipeline_state(bare):
    client, registry, tmp_path = bare
    registry.create_project(name="Shop API", repo_mode="create_new")
    requests = client  # unused marker for clarity
    client.post("/requests/new", data={"project_id": "shop-api", "kind": "feature", "text": "x", "extra": ""})
    snapshot = client.get("/status").json()
    assert snapshot["requests"][0]["status"] == "submitted"
    client.post("/requests/req-1/run")
    snapshot = client.get("/status").json()
    assert snapshot["requests"][0]["status"] == "awaiting_approval"
    assert snapshot["requests"][0]["total"] == 25
