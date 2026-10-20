from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.models_config import ModelsSettings
from caliper.registry import ProjectRegistry
from caliper.state import RequestStore


def _client(tmp_path, probe=None):
    registry = ProjectRegistry(tmp_path / "projects")
    requests = RequestStore(tmp_path / "state.sqlite3")
    settings = ModelsSettings(tmp_path / "models.yaml", tmp_path / "secrets")
    app = create_app(registry=registry, requests=requests, models_settings=settings, probe=probe)
    return TestClient(app), registry, requests, settings


def _form(models=None, keys=None, action="save"):
    models = models or {
        "planning": "planning-model",
        "execution": "execution-model",
        "review": "review-model",
    }
    keys = keys or {}
    data = {"action": action}
    for index, role in enumerate(("planning", "execution", "review")):
        data[f"{role}_base_url"] = f"http://127.0.0.1:{9000 + index}/v1"
        data[f"{role}_model"] = models[role]
        data[f"{role}_api_key"] = keys.get(role, "")
    return data


def test_settings_page_shows_three_roles_without_secret_values(tmp_path):
    client, *_ = _client(tmp_path)
    response = client.get("/settings/models")
    assert response.status_code == 200
    for label in ("Planning", "Execution", "Review"):
        assert label in response.text
    assert 'type="password"' in response.text
    assert "Not configured" in response.text
    assert "sk-" not in response.text


def test_unconfigured_project_blocks_request_run_with_reason(tmp_path):
    client, registry, requests, _ = _client(tmp_path)
    project = registry.create_project("Sandbox", "https://github.com/example/sandbox")
    req = requests.create(project["id"], "feature", "Add a refund endpoint")
    response = client.get(f"/requests/{req['id']}")
    assert response.status_code == 200
    assert "Models are not configured" in response.text
    assert "Run workflow" not in response.text


def test_save_rejects_duplicate_models_and_writes_nothing(tmp_path):
    client, _, _, settings = _client(tmp_path)
    response = client.post("/settings/models", data=_form({"planning": "same", "execution": "same", "review": "review-model"}))
    assert response.status_code == 422
    assert "different model" in response.text.lower()
    assert not settings.path.exists()


def test_save_persists_models_and_keys_outside_yaml(tmp_path):
    client, _, _, settings = _client(tmp_path)
    response = client.post("/settings/models", data=_form(keys={"planning": "sk-planning"}), follow_redirects=False)
    assert response.status_code == 303
    assert settings.configured() is True
    assert settings.api_key("planning") == "sk-planning"
    assert "sk-planning" not in settings.path.read_text(encoding="utf-8")
    page = client.get("/settings/models?saved=1")
    assert "Models saved" in page.text
    assert "Configured" in page.text


def test_test_connection_reports_success_and_failure(tmp_path):
    def ok_probe(role, base_url, model, api_key):
        return "OK"

    def bad_probe(role, base_url, model, api_key):
        raise RuntimeError("connection refused")

    client, *_ = _client(tmp_path, probe=ok_probe)
    page = client.post("/settings/models/test", data=_form(action="test:planning"))
    assert page.status_code == 200
    assert "Connection OK" in page.text
    assert "OK" in page.text

    client, *_ = _client(tmp_path / "bad", probe=bad_probe)
    page = client.post("/settings/models/test", data=_form(action="test:review"))
    assert page.status_code == 200
    assert "Connection failed" in page.text
    assert "connection refused" in page.text
