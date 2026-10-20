import pytest
from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.registry import ProjectRegistry
from caliper.state import RequestStore


@pytest.fixture()
def client(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_mode="create_new")
    requests = RequestStore(tmp_path / "state.sqlite3")
    return TestClient(create_app(registry=registry, requests=requests))


def test_project_page_shows_empty_requests_state(client):
    response = client.get("/projects/shop-api")
    assert response.status_code == 200
    assert "No requests yet" in response.text
    assert "Add feature" in response.text


def test_composer_shows_one_box_and_kind_toggle(client):
    response = client.get("/projects/shop-api/requests/new")
    assert response.status_code == 200
    assert 'name="kind"' in response.text
    assert 'name="text"' in response.text
    assert "Add error / log" in response.text


def test_submit_feature_request_redirects_and_shows_in_list(client):
    response = client.post(
        "/projects/shop-api/requests/new",
        data={"kind": "feature", "text": "Add refund endpoint", "extra": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "req-1" in response.text
    assert "Add refund endpoint" in response.text

    project = client.get("/projects/shop-api")
    assert "req-1" in project.text
    assert "Add refund endpoint" in project.text


def test_submit_with_log_stores_extras(client):
    response = client.post(
        "/projects/shop-api/requests/new",
        data={"kind": "bugfix", "text": "Rounding error", "extra": "ValueError: precision"},
        follow_redirects=True,
    )
    assert "TypeError" not in response.text
    assert "Rounding error" in response.text


def test_blank_text_shows_error_not_stacktrace(client):
    response = client.post(
        "/projects/shop-api/requests/new",
        data={"kind": "feature", "text": "", "extra": ""},
    )
    assert response.status_code == 422
    assert "required" in response.text
    assert "Traceback" not in response.text


def test_request_page_missing_returns_guidance(client):
    response = client.get("/requests/nope")
    assert response.status_code == 404
    assert "does not exist" in response.text
