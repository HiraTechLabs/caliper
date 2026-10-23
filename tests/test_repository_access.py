"""Repository access management: replace, activate, discard and remove deploy keys.

The key generator stores obviously fake key material; no real private key is
created or displayed here. Repository access checks use injected testers, so
no real repository is contacted by the API-level tests.
"""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.registry import ProjectRegistry
from caliper.remote import ReadCheckError, WriteCheckError
from caliper.secrets import SecretStoreError
from caliper.state import RequestStore


class Training:
    """Contributed access checks: decide the verdict per key material."""

    def __init__(self, results=None, default="Read and write access verified."):
        self.results = results or {}
        self.default = default
        self.calls = []

    def __call__(self, url, key_path):
        private = Path(key_path).read_text(encoding="utf-8")
        self.calls.append(private)
        result = self.results.get(private, self.default)
        if isinstance(result, Exception):
            raise result
        return str(result)


class RefusingStore:
    """Wrapper that refuses writes for chosen names. Messages never contain values."""

    def __init__(self, store, refused_names=()):
        self._store = store
        self._refused = frozenset(refused_names)

    def __getattr__(self, name):
        return getattr(self._store, name)

    def put(self, scope, name, value):
        if name in self._refused:
            raise SecretStoreError(f"Cannot write secret {scope}/{name}: disk full.")
        return self._store.put(scope, name, value)


def _key_generator(path: Path, comment: str) -> str:
    """Fake deterministic pair; the private string is obviously not a real key."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"PRIVATE-{comment}", encoding="utf-8")
    public = path.with_name(path.name + ".pub")
    public.write_text(f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI{comment}", encoding="utf-8")
    return public.read_text(encoding="utf-8")


def _base(tmp_path, tester, store=None):
    registry = ProjectRegistry(
        tmp_path / "projects",
        secrets_dir=tmp_path / "secrets",
        key_generator=_key_generator,
        repo_tester=tester,
    )
    if store is not None:
        registry.secret_store = store
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    client = TestClient(
        create_app(registry=registry, requests=RequestStore(tmp_path / "state.sqlite3"))
    )
    return registry, client


@pytest.fixture()
def app(tmp_path):
    tester = Training()
    registry, client = _base(tmp_path, tester)
    client.post("/api/projects/shop-api/repository/key")
    return client, registry, tester


@pytest.fixture()
def multi(tmp_path):
    tester = Training()
    registry, client = _base(tmp_path, tester)
    registry.create_project(name="Other One", repo_url="https://github.com/example/other-one")
    return client, registry


# --- backward compatibility ------------------------------------------------------

def test_a_manifest_without_the_new_fields_reports_sane_defaults(tmp_path):
    registry, _ = _base(tmp_path, Training())
    path = registry.projects_root / "shop-api" / "manifest.yaml"
    legacy = yaml.safe_load(path.read_text(encoding="utf-8"))
    for field in ("access", "rotated_at", "remote_revocation_pending"):
        legacy["repo"].pop(field, None)
    path.write_text(yaml.safe_dump(legacy, sort_keys=False), encoding="utf-8")

    status = registry.repository_status("shop-api")

    assert status["access"] is None
    assert status["required_access"] == "write"
    assert status["replacement"] is None
    assert status["remote_revocation_pending"] is False


# --- replacement lifecycle ---------------------------------------------------------

def test_replacement_is_staged_and_the_active_key_keeps_working(app):
    client, registry, _ = app
    old_public = registry.public_deploy_key("shop-api")
    old_private = registry.secret_store.get("project/shop-api", "deploy_key")

    response = client.post("/api/projects/shop-api/repository/key/replace")

    assert response.status_code == 200
    store = registry.secret_store
    status = response.json()["repository"]
    replacement = status["replacement"]
    assert replacement["public_key"].startswith("ssh-ed25519")
    assert replacement["public_key"] != old_public
    assert replacement["verified"] is False
    assert "keeps working" in replacement["message"]
    # The active key is untouched until the replacement verifies.
    assert store.get("project/shop-api", "deploy_key") == old_private
    assert registry.public_deploy_key("shop-api") == old_public
    assert status["verified"] is False


def test_a_failing_verification_keeps_the_active_key(app):
    client, registry, tester = app
    tester.results = {
        "PRIVATE-caliper-shop-api-replacement": WriteCheckError("Write check failed: denied."),
    }
    client.post("/api/projects/shop-api/repository/key/replace")
    old_private = registry.secret_store.get("project/shop-api", "deploy_key")

    response = client.post("/api/projects/shop-api/repository/key/activate")

    assert response.status_code == 422
    body = response.json()
    assert "still active" in body["error"]
    assert "Write check failed: denied" in body["error"]
    store = registry.secret_store
    assert store.get("project/shop-api", "deploy_key") == old_private
    assert store.exists("project/shop-api", "deploy_key_replacement")
    status = body["repository"]
    assert status["verified"] is False
    assert status["replacement"]["public_key"] == store.get(
        "project/shop-api", "deploy_key_replacement.pub"
    )
    # The tester saw the replacement key, not the active one.
    assert tester.calls == ["PRIVATE-caliper-shop-api-replacement"]


def test_a_verified_replacement_becomes_the_active_key(app):
    client, registry, tester = app
    replacement_public = client.post(
        "/api/projects/shop-api/repository/key/replace"
    ).json()["repository"]["replacement"]["public_key"]
    replacement_private = registry.secret_store.get(
        "project/shop-api", "deploy_key_replacement"
    )
    assert replacement_private != registry.secret_store.get("project/shop-api", "deploy_key")

    response = client.post("/api/projects/shop-api/repository/key/activate")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    status = body["repository"]
    store = registry.secret_store
    assert store.get("project/shop-api", "deploy_key") == replacement_private
    assert status["public_key"] == replacement_public
    assert status["verified"] is True
    assert status["access"] == "write"
    assert status["rotated_at"] is not None
    assert status["remote_revocation_pending"] is True
    assert status["replacement"] is None
    # The staged pair is cleaned up after promotion.
    assert not store.exists("project/shop-api", "deploy_key_replacement")
    assert Path(status["key_path"]).read_text(encoding="utf-8").strip() == replacement_public
    assert tester.calls == ["PRIVATE-caliper-shop-api-replacement"]


def test_discarding_the_replacement_leaves_the_active_key(app):
    client, registry, _ = app
    old_private = registry.secret_store.get("project/shop-api", "deploy_key")
    client.post("/api/projects/shop-api/repository/key/replace")

    response = client.post("/api/projects/shop-api/repository/key/discard")

    assert response.status_code == 200
    store = registry.secret_store
    assert store.get("project/shop-api", "deploy_key") == old_private
    assert not store.exists("project/shop-api", "deploy_key_replacement")
    assert response.json()["repository"]["replacement"] is None


def test_activation_needs_a_staged_replacement(app):
    client, _, _ = app
    response = client.post("/api/projects/shop-api/repository/key/activate")
    assert response.status_code == 422
    assert "No replacement key is staged" in response.json()["error"]


def test_replacement_requires_a_repository_url(tmp_path):
    registry, _ = _base(tmp_path, Training())
    manifest = registry.manifest("shop-api")
    manifest["repo"]["url"] = ""
    registry._write_manifest("shop-api", manifest)
    with pytest.raises(Exception, match="repository URL"):
        registry.generate_replacement_deploy_key("shop-api")


def test_a_secret_store_failure_during_replacement_leaves_the_active_key(tmp_path):
    tester = Training()
    registry, client = _base(tmp_path, tester)
    client.post("/api/projects/shop-api/repository/key")
    old_private = registry.secret_store.get("project/shop-api", "deploy_key")
    registry.secret_store = RefusingStore(
        registry.secret_store, refused_names={"deploy_key_replacement"}
    )

    response = client.post("/api/projects/shop-api/repository/key/replace")

    assert response.status_code == 422
    assert "disk full" in response.json()["error"]
    # The active pair is still there and usable.
    registry.secret_store = registry.secret_store._store
    assert registry.secret_store.get("project/shop-api", "deploy_key") == old_private
    assert registry.secret_store.exists("project/shop-api", "deploy_key.pub")
    assert not registry.secret_store.exists("project/shop-api", "deploy_key_replacement")


# --- credential removal ------------------------------------------------------------

def test_removing_credentials_keeps_the_project(app):
    client, registry, _ = app
    key_status = client.get("/api/projects/shop-api/repository").json()
    public_path = Path(key_status["key_path"])
    assert public_path.is_file()

    response = client.delete("/api/projects/shop-api/repository/key")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    store = registry.secret_store
    assert store.list("project/shop-api") == []
    assert not public_path.exists()
    status = body["repository"]
    assert status["key_exists"] is False
    assert status["verified"] is False
    assert "Deploy key removed" in status["test_message"]
    # The project itself and its recorded data stay.
    assert registry.get_project("shop-api")["name"] == "Shop API"
    assert client.get("/projects/shop-api").status_code == 200


def test_removing_credentials_twice_does_not_fail(app):
    client, _, _ = app
    assert client.delete("/api/projects/shop-api/repository/key").status_code == 200
    assert client.delete("/api/projects/shop-api/repository/key").status_code == 200


# --- project isolation ---------------------------------------------------------------

def test_credentials_stay_scoped_to_their_project(multi):
    client, registry = multi
    client.post("/api/projects/shop-api/repository/key")
    client.post("/api/projects/other-one/repository/key")
    other_public = registry.public_deploy_key("other-one")

    client.post("/api/projects/shop-api/repository/key/replace")
    other_status = client.get("/api/projects/other-one/repository").json()
    assert other_status["replacement"] is None
    assert other_status["public_key"] == other_public

    client.post("/api/projects/other-one/repository/key/discard")
    client.delete("/api/projects/shop-api/repository/key")

    store = registry.secret_store
    assert registry.public_deploy_key("other-one") == other_public
    assert store.exists("project/other-one", "deploy_key")
    assert store.list("project/other-one") == ["deploy_key", "deploy_key.pub"]
    assert client.get("/api/projects/other-one/repository").json()["remote_revocation_pending"] is False


# --- secret hygiene ------------------------------------------------------------------

def test_private_key_material_never_leaves_the_secret_store(app):
    client, registry, _ = app
    client.post("/api/projects/shop-api/repository/key/replace")
    checks = [
        ("GET", "/api/projects/shop-api/repository"),
        ("GET", "/projects/shop-api"),
        ("POST", "/api/projects/shop-api/repository/key/replace"),
        ("POST", "/api/projects/shop-api/repository/key/activate"),
        ("POST", "/api/projects/shop-api/repository/key/discard"),
        ("DELETE", "/api/projects/shop-api/repository/key"),
    ]
    for method, url in checks:
        response = getattr(client, method.lower())(url)
        assert "PRIVATE-caliper" not in response.text, method
    stored_manifest = (registry.projects_root / "shop-api" / "manifest.yaml").read_text(
        encoding="utf-8"
    )
    assert "PRIVATE-caliper" not in stored_manifest


# --- connection result semantics ------------------------------------------------------

def test_a_write_check_failure_marks_the_key_read_only(app):
    client, registry, tester = app
    tester.default = WriteCheckError("Write check failed: read-only deploy key.")

    response = client.post("/api/projects/shop-api/repository/test")

    assert response.status_code == 422
    assert "read-only" in response.json()["error"]
    status = registry.repository_status("shop-api")
    assert status["access"] == "read"
    assert status["verified"] is False


def test_a_read_check_failure_clears_the_access_level(app):
    client, registry, tester = app
    tester.default = ReadCheckError("Read check failed: permission denied.")

    response = client.post("/api/projects/shop-api/repository/test")

    assert response.status_code == 422
    status = registry.repository_status("shop-api")
    assert status["access"] is None
    assert status["verified"] is False


def test_all_forms_render_after_the_lifecycle(app):
    client, registry, _ = app
    staging = client.post("/projects/shop-api/repository/key/replace", follow_redirects=False)
    assert staging.status_code == 303
    page = client.get("/projects/shop-api")
    assert "Replacement key (not active yet)" in page.text
    assert "Test replacement and activate" in page.text

    activated = client.post("/projects/shop-api/repository/key/activate", follow_redirects=False)
    assert activated.status_code == 303
    page = client.get("/projects/shop-api")
    assert "Rotation pending on GitHub" in page.text
    assert "Active public deploy key" in page.text
    assert "Remove repository access" in page.text

    removed = client.post("/projects/shop-api/repository/remove", follow_redirects=False)
    assert removed.status_code == 303
    assert registry.repository_status("shop-api")["key_exists"] is False
