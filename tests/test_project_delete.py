import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from caliper.deletion import ProjectDeleteError, ProjectDeleter
from caliper.main import create_app
from caliper.registry import ProjectError, ProjectRegistry
from caliper.remote import RemoteError
from caliper.state import RequestStore
from tests.support import complete_setup


def _key_generator(path: Path, comment: str) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("PRIVATE KEY", encoding="utf-8")
    public = path.with_name(path.name + ".pub")
    public.write_text(f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI{comment}", encoding="utf-8")
    return public.read_text(encoding="utf-8")


@pytest.fixture()
def setup(tmp_path):
    registry = ProjectRegistry(
        tmp_path / "projects",
        secrets_dir=tmp_path / "secrets",
        key_generator=_key_generator,
        repo_tester=lambda url, key: "Read and write access verified.",
        env_tester=lambda host, user, key: "SSH and docker compose are reachable.",
    )
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    complete_setup(registry, "shop-api")
    registry.ensure_deploy_key("shop-api")
    for env in ("dev", "prod"):
        registry.ensure_environment_key("shop-api", env)
    requests = RequestStore(tmp_path / "state.sqlite3")
    return registry, requests, tmp_path


def _add_files(registry, tmp_path):
    project = registry.projects_root / "shop-api"
    (project / "worktrees" / "req-1").mkdir(parents=True)
    (project / "worktrees" / "req-1" / "branch.txt").write_text("caliper/req-1", encoding="utf-8")
    remote = tmp_path / "github" / "shop-api" / ".caliper" / "principles"
    remote.mkdir(parents=True)
    (remote / "security.md").write_text("SEC-001 stays in Git.", encoding="utf-8")
    return remote


def _audit_rows(requests):
    connection = sqlite3.connect(requests.db_path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM audit_events ORDER BY id")]
    finally:
        connection.close()


def test_delete_removes_project_files_requests_and_secrets_and_writes_audit(setup):
    registry, requests, tmp_path = setup
    remote = _add_files(registry, tmp_path)
    req = requests.create("shop-api", "feature", "Add refunds")

    result = ProjectDeleter(registry, requests, tmp_path).delete("shop-api")

    assert result["deleted"] is True
    assert result["requests"] == 1
    assert result["cancelled_requests"] == [req["id"]]
    assert not (registry.projects_root / "shop-api").exists()
    assert not (tmp_path / "secrets" / "projects" / "shop-api").exists()
    assert requests.list_for_project("shop-api") == []
    assert (remote / "security.md").read_text(encoding="utf-8") == "SEC-001 stays in Git."

    audits = _audit_rows(requests)
    assert len(audits) == 1
    assert audits[0]["project_id"] == "shop-api"
    assert audits[0]["project_name"] == "Shop API"
    details = json.loads(audits[0]["details"])
    assert details["removed"]["requests"] == 1
    assert details["kept"]["caliper_files"] == ".caliper/ files in the repository"


class ObservingStore(RequestStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_before_delete = []

    def delete_project_with_audit(self, project_id, project_name, details):
        self.seen_before_delete = [
            (row["id"], row["status"]) for row in self.list_for_project(project_id)
        ]
        return super().delete_project_with_audit(project_id, project_name, details)


def test_active_requests_are_cancelled_before_history_is_deleted(tmp_path):
    registry = ProjectRegistry(
        tmp_path / "projects", secrets_dir=tmp_path / "secrets", key_generator=_key_generator,
    )
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    requests = ObservingStore(tmp_path / "state.sqlite3")
    req = requests.create("shop-api", "bugfix", "Fix refunds")

    ProjectDeleter(registry, requests, tmp_path).delete("shop-api")

    assert requests.seen_before_delete == [(req["id"], "cancelled")]
    assert requests.get(req["id"]) is None


def test_undeploy_failure_aborts_and_leaves_everything_intact(setup):
    registry, requests, tmp_path = setup
    remote = _add_files(registry, tmp_path)
    req = requests.create("shop-api", "feature", "Add refunds")
    before_updated = requests.get(req["id"])["updated_at"]
    before_manifest = (registry.projects_root / "shop-api" / "manifest.yaml").read_text(encoding="utf-8")

    def failing_stopper(host, user, deploy_path, compose_file, key_path, known_hosts_path=None):
        raise RemoteError("SSH connection failed: permission denied")

    deleter = ProjectDeleter(registry, requests, tmp_path, stopper=failing_stopper)
    with pytest.raises(ProjectDeleteError, match="permission denied"):
        deleter.delete("shop-api", stop_dev=True)

    after = requests.get(req["id"])
    assert after["status"] == "submitted"
    assert after["updated_at"] == before_updated
    assert (registry.projects_root / "shop-api" / "manifest.yaml").read_text(encoding="utf-8") == before_manifest
    assert (registry.deploy_key_path("shop-api")).is_file()
    assert (registry.environment_key_path("shop-api", "dev")).is_file()
    assert (remote / "security.md").is_file()
    assert _audit_rows(requests) == []


def test_delete_rejects_parent_traversal_and_leaves_outside_files(setup):
    registry, requests, tmp_path = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ProjectError):
        ProjectDeleter(registry, requests, tmp_path).delete("../outside")

    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (registry.projects_root / "shop-api" / "manifest.yaml").is_file()


def test_delete_rejects_symlinked_project_directory(setup):
    registry, requests, tmp_path = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    registry.project_path("shop-api").rename(outside / "shop-api")
    link = registry.project_path("shop-api")
    link.symlink_to(outside / "shop-api", target_is_directory=True)

    try:
        with pytest.raises(ProjectDeleteError, match="symlink"):
            ProjectDeleter(registry, requests, tmp_path).delete("shop-api")
        assert (outside / "shop-api" / "manifest.yaml").is_file()
        assert _audit_rows(requests) == []
    finally:
        link.unlink()


def test_delete_rejects_storage_root_mismatch(setup):
    registry, requests, tmp_path = setup
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    with pytest.raises(ProjectDeleteError, match="CALIPER_DATA_DIR"):
        ProjectDeleter(registry, requests, elsewhere).delete("shop-api")

    assert (registry.projects_root / "shop-api" / "manifest.yaml").is_file()


def test_stop_switches_are_disabled_when_environments_are_not_configured(tmp_path):
    registry = ProjectRegistry(
        tmp_path / "projects", secrets_dir=tmp_path / "secrets", key_generator=_key_generator,
    )
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    client = TestClient(create_app(registry=registry, requests=requests))

    page = client.get("/projects/shop-api")
    assert "data-stop-dev disabled" in page.text
    assert "data-stop-prod disabled" in page.text
    assert "Configure Dev" in page.text
    assert "Configure Prod" in page.text


def test_stop_switches_are_enabled_when_environments_are_ready(setup):
    registry, requests, _ = setup
    client = TestClient(create_app(registry=registry, requests=requests))
    page = client.get("/projects/shop-api")
    assert "data-stop-dev disabled" not in page.text
    assert "data-stop-prod disabled" not in page.text


def test_optional_undeploys_run_and_success_returns_audit(setup):
    registry, requests, tmp_path = setup
    calls = []

    def stopper(host, user, deploy_path, compose_file, key_path, known_hosts_path=None):
        calls.append((host, user, deploy_path, compose_file, Path(key_path).name))
        return "Deployment stopped. Volumes kept."

    result = ProjectDeleter(registry, requests, tmp_path, stopper=stopper).delete(
        "shop-api", stop_dev=True, stop_prod=True,
    )

    assert [call[0] for call in calls] == ["dev.example.com", "prod.example.com"]
    assert [call[4] for call in calls] == ["env_dev", "env_prod"]
    assert result["deployments_stopped"] == ["dev", "prod"]
    assert result["audit_id"] > 0


def test_delete_api_reports_failure_then_deletes_and_ui_has_confirmation(setup):
    registry, requests, tmp_path = setup
    remote = _add_files(registry, tmp_path)
    requests.create("shop-api", "feature", "Add refunds")

    def failing_stopper(host, user, deploy_path, compose_file, key_path, known_hosts_path=None):
        raise RemoteError("host is unreachable")

    deleter = ProjectDeleter(registry, requests, tmp_path, stopper=failing_stopper)
    client = TestClient(create_app(registry=registry, requests=requests, project_deleter=deleter))

    page = client.get("/projects/shop-api")
    assert page.status_code == 200
    assert "Danger zone" in page.text
    assert 'id="delete-project-dialog"' in page.text
    assert "Delete Shop API" in page.text
    assert "Type <strong>Shop API</strong> to confirm" in page.text
    assert "Will stay: the GitHub repository" in page.text

    failed = client.delete("/api/projects/shop-api?stop_dev=true")
    assert failed.status_code == 409
    assert failed.json()["error"].startswith("Could not stop Dev: host is unreachable")
    assert client.get("/projects/shop-api").status_code == 200
    assert (remote / "security.md").is_file()

    deleted = client.delete("/api/projects/shop-api")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/projects/shop-api").status_code == 404
    assert client.delete("/api/projects/shop-api").status_code == 404
