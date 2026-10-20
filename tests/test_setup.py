from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.registry import ProjectRegistry
from caliper.remote import RemoteError
from caliper.state import RequestStore


def _key_generator(path: Path, comment: str) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("PRIVATE KEY", encoding="utf-8")
    public = path.with_name(path.name + ".pub")
    public.write_text(f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI{comment}", encoding="utf-8")
    return public.read_text(encoding="utf-8")


@pytest.fixture()
def app(tmp_path, repo_tester=None, env_tester=None):
    def default_repo_tester(url, key_path):
        return "Read and write access verified."

    def default_env_tester(host, user, key_path, **kwargs):
        return {"message": "SSH, docker compose and the deploy path are ready.",
                "host_key_fingerprint": "SHA256:TESTFINGERPRINT"}

    registry = ProjectRegistry(
        tmp_path / "projects",
        secrets_dir=tmp_path / "secrets",
        key_generator=_key_generator,
        repo_tester=repo_tester or default_repo_tester,
        env_tester=env_tester or default_env_tester,
    )
    registry.create_project(
        name="Shop API", repo_mode="existing", repo_url="https://github.com/example/shop-api"
    )
    requests = RequestStore(tmp_path / "state.sqlite3")
    client = TestClient(create_app(registry=registry, requests=requests))
    return client, registry, tmp_path


def test_repository_key_is_generated_in_the_secrets_dir(app):
    client, registry, tmp_path = app
    body = client.get("/api/projects/shop-api/repository").json()
    assert body["public_key"] == ""
    assert body["key_exists"] is False
    assert body["keys_url"] == "https://github.com/example/shop-api/settings/keys"

    response = client.post("/api/projects/shop-api/repository/key")
    assert response.status_code == 200
    body = response.json()
    assert body["public_key"].startswith("ssh-ed25519")
    assert body["key_exists"] is True
    key_path = tmp_path / "secrets" / "projects" / "shop-api" / "repo_deploy"
    assert key_path.read_text(encoding="utf-8") == "PRIVATE KEY"
    assert registry.manifest("shop-api")["repo"]["key_created_at"]
    assert registry.manifest("shop-api")["repo"]["verified"] is False


def test_repository_access_test_verifies_read_and_write(app):
    client, registry, _ = app
    client.post("/api/projects/shop-api/repository/key")
    response = client.post("/api/projects/shop-api/repository/test")
    assert response.status_code == 200
    assert response.json()["message"] == "Read and write access verified."
    assert registry.manifest("shop-api")["repo"]["verified"] is True


def test_a_failed_repository_test_is_reported_plainly(tmp_path):
    def failing_repo_tester(url, key_path):
        raise RemoteError("Read check failed: Permission denied (publickey).")

    registry = ProjectRegistry(
        tmp_path / "projects",
        secrets_dir=tmp_path / "secrets",
        key_generator=_key_generator,
        repo_tester=failing_repo_tester,
    )
    registry.create_project(
        name="Shop API", repo_mode="existing", repo_url="https://github.com/example/shop-api"
    )
    client = TestClient(create_app(registry=registry, requests=RequestStore(tmp_path / "state.sqlite3")))

    response = client.post("/api/projects/shop-api/repository/test")
    assert response.status_code == 422
    assert "Permission denied" in response.json()["error"]
    assert registry.manifest("shop-api")["repo"]["verified"] is False


def test_a_project_without_a_repository_url_cannot_be_tested(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects", secrets_dir=tmp_path / "secrets", key_generator=_key_generator)
    registry.create_project(name="Shop API", repo_mode="create_new")
    client = TestClient(create_app(registry=registry, requests=RequestStore(tmp_path / "state.sqlite3")))

    response = client.post("/api/projects/shop-api/repository/test")
    assert response.status_code == 422
    assert "repository URL" in response.json()["error"]


def test_missing_project_returns_a_plain_error(app):
    client, _, _ = app
    response = client.get("/api/projects/ghost/repository")
    assert response.status_code == 404
    assert "does not exist" in response.json()["error"]
