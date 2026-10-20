import pytest
from fastapi.testclient import TestClient

from caliper.registry import ProjectRegistry
from caliper.main import create_app


@pytest.fixture()
def client(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects")
    return TestClient(create_app(registry=registry))


def test_health_endpoint_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_project_list_empty_state(client):
    response = client.get("/projects")
    assert response.status_code == 200
    assert "No projects yet" in response.text
    assert "New project" in response.text

    dashboard = client.get("/")
    assert "All clear" in dashboard.text
    assert "New request" in dashboard.text


def test_create_project_via_form_shows_in_list_and_detail(client):
    response = client.post(
        "/projects/new",
        data={"name": "Shop API", "repo_mode": "existing", "repo_url": "https://git.example.com/shop/api.git", "stack_preset": "python-fastapi"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Shop API" in response.text
    assert "https://git.example.com/shop/api.git" in response.text
    assert "Product:" not in response.text


    listing = client.get("/projects")
    assert "Shop API" in listing.text

    detail = client.get("/projects/shop-api")
    assert "Environments" in detail.text
    assert "Dev" in detail.text and "Prod" in detail.text
    assert "Security" in detail.text
    assert "SEC-001" in detail.text


def test_create_new_repo_shows_repo_pending(client):
    response = client.post(
        "/projects/new",
        data={"name": "Fresh App", "repo_mode": "create_new", "repo_url": "", "stack_preset": "python-fastapi"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "to be created" in response.text


def test_form_error_is_shown_not_stacktrace(client):
    response = client.post(
        "/projects/new",
        data={"name": "", "repo_mode": "create_new", "repo_url": "", "stack_preset": "python-fastapi"},
    )
    assert response.status_code == 422
    assert "Project name is required" in response.text
    assert "Traceback" not in response.text


def test_missing_project_page_has_guidance(client):
    response = client.get("/projects/ghost")
    assert response.status_code == 404
    assert "does not exist" in response.text
    assert "New project" in response.text
