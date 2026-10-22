import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.principles import (
    APPROVED,
    DRAFT,
    MISSING,
    PRINCIPLE_ORDER,
    principle_starter,
    rule_ids,
    validate_for_approval,
)
from caliper.registry import ProjectRegistry
from caliper.state import RequestStore
from tests.support import make_git_repo

RULES = """# Security

- **SEC-001**: All external input is validated at the boundary.
- **SEC-002**: Secrets never end up in the repository.
"""


def _key_generator(path: Path, comment: str) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("PRIVATE KEY", encoding="utf-8")
    public = path.with_name(path.name + ".pub")
    public.write_text(f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI{comment}", encoding="utf-8")
    return public.read_text(encoding="utf-8")


@pytest.fixture()
def repo(tmp_path):
    bare = tmp_path / "remote" / "shop-api.git"
    url = make_git_repo(bare)
    return bare, url


@pytest.fixture()
def app(tmp_path, repo):
    bare, url = repo
    registry = ProjectRegistry(
        tmp_path / "projects", secrets_dir=tmp_path / "secrets", key_generator=_key_generator
    )
    registry.create_project(name="Shop API", repo_url=url)
    registry.ensure_deploy_key("shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    return TestClient(create_app(registry=registry, requests=requests)), registry, bare


def _remote_file(bare: Path, path: str) -> str:
    result = subprocess.run(
        ["git", "--git-dir", str(bare), "show", f"main:{path}"],
        capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def test_new_principle_contains_no_generated_rules(app):
    client, _, _ = app
    listing = client.get("/api/projects/shop-api/principles").json()
    assert [item["key"] for item in listing["principles"]] == PRINCIPLE_ORDER
    for item in listing["principles"]:
        assert item["state"] == MISSING
        assert item["content"] == ""
        assert item["rule_ids"] == []
        starter = item["starter"]
        assert rule_ids(starter) == []
        lines = [line for line in starter.strip().splitlines() if line.strip()]
        assert len(lines) == 2


def test_put_writes_the_file_to_the_repository_default_branch(app):
    client, _, bare = app
    response = client.put(
        "/api/projects/shop-api/principles/security", json={"content": RULES}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == DRAFT
    assert body["commit"]
    assert _remote_file(bare, ".caliper/principles/security.md") == RULES


def test_put_without_content_creates_a_starter_without_rules(app):
    client, _, bare = app
    response = client.put("/api/projects/shop-api/principles/security", json={})
    assert response.status_code == 200
    written = _remote_file(bare, ".caliper/principles/security.md")
    assert written == principle_starter("security")
    assert rule_ids(written) == []


def test_put_without_content_refuses_to_overwrite_an_existing_file(app):
    client, _, _ = app
    client.put("/api/projects/shop-api/principles/security", json={"content": RULES})
    response = client.put("/api/projects/shop-api/principles/security", json={})
    assert response.status_code == 422
    assert "Content is required" in response.json()["error"]


def test_approve_records_the_content_hash_and_the_commit(app):
    client, registry, _ = app
    client.put("/api/projects/shop-api/principles/security", json={"content": RULES})
    response = client.post("/api/projects/shop-api/principles/security/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == APPROVED
    assert body["approved_commit"]
    assert body["rule_ids"] == ["SEC-001", "SEC-002"]
    record = registry.principle_records("shop-api")["security"]
    assert record["state"] == APPROVED
    assert record["approved_commit"] == body["approved_commit"]
    assert record["content_hash"] == body["content_hash"]


def test_approve_rejects_a_principle_without_rules(app):
    client, registry, _ = app
    client.put("/api/projects/shop-api/principles/security", json={"content": "# Security\n\nNo rules yet.\n"})
    response = client.post("/api/projects/shop-api/principles/security/approve")
    assert response.status_code == 422
    assert "no numbered rules" in response.json()["error"]
    # The file stays a draft; it was never approved.
    assert registry.principle_records("shop-api")["security"]["state"] == DRAFT
    assert registry.principle_records("shop-api")["security"]["approved_commit"] is None


def test_approve_rejects_duplicate_rule_ids(app):
    client, registry, _ = app
    duplicated = "# Security\n\n- SEC-001: first\n- SEC-001: second\n"
    client.put("/api/projects/shop-api/principles/security", json={"content": duplicated})
    response = client.post("/api/projects/shop-api/principles/security/approve")
    assert response.status_code == 422
    assert "unique" in response.json()["error"]
    assert "SEC-001" in response.json()["error"]
    assert registry.principle_records("shop-api")["security"]["state"] == DRAFT
    assert registry.principle_records("shop-api")["security"]["approved_commit"] is None


def test_approve_rejects_a_file_that_is_not_in_the_repository(app):
    client, registry, _ = app
    response = client.post("/api/projects/shop-api/principles/security/approve")
    assert response.status_code == 422
    assert "not in the repository" in response.json()["error"]
    assert registry.principle_records("shop-api")["security"]["state"] == MISSING


def test_editing_an_approved_principle_resets_it_to_draft(app):
    client, registry, _ = app
    client.put("/api/projects/shop-api/principles/security", json={"content": RULES})
    client.post("/api/projects/shop-api/principles/security/approve")
    assert registry.principle_records("shop-api")["security"]["state"] == APPROVED

    edited = RULES + "- **SEC-003**: Logs never contain secrets.\n"
    response = client.put("/api/projects/shop-api/principles/security", json={"content": edited})
    assert response.status_code == 200
    assert response.json()["state"] == DRAFT
    record = registry.principle_records("shop-api")["security"]
    assert record["state"] == DRAFT
    assert record["approved_commit"] is None
    assert record["approved_at"] is None
    assert record["rule_ids"] == []


def test_sync_reads_the_principles_from_the_repository(app):
    client, registry, _ = app
    client.put("/api/projects/shop-api/principles/security", json={"content": RULES})

    response = client.post("/api/projects/shop-api/principles/sync")
    assert response.status_code == 200
    body = response.json()
    security = [item for item in body["principles"] if item["key"] == "security"][0]
    assert security["state"] == DRAFT
    assert security["content"] == RULES
    assert security["rule_ids"] == []
    assert body["default_branch"] == "main"


def test_sync_detects_a_file_edited_outside_caliper_and_drops_the_approval(app, repo):
    client, registry, bare = app
    client.put("/api/projects/shop-api/principles/security", json={"content": RULES})
    client.post("/api/projects/shop-api/principles/security/approve")
    assert registry.principle_records("shop-api")["security"]["state"] == APPROVED

    work = bare.parent / "shop-api.git-work"
    subprocess.run(["git", "-C", str(work), "fetch", "origin", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "reset", "--hard", "origin/main"], check=True, capture_output=True)
    (work / ".caliper" / "principles").mkdir(parents=True, exist_ok=True)
    (work / ".caliper" / "principles" / "security.md").write_text(
        "# Security\n\n- SEC-001: changed by a human in the repository\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(work), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "-c", "user.name=Human", "-c", "user.email=human@example.com",
         "commit", "-qm", "edit the security principle"],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True, capture_output=True)

    response = client.post("/api/projects/shop-api/principles/sync")
    assert response.status_code == 200
    security = [item for item in response.json()["principles"] if item["key"] == "security"][0]
    assert security["state"] == DRAFT
    assert registry.principle_records("shop-api")["security"]["state"] == DRAFT
    assert registry.principle_records("shop-api")["security"]["approved_commit"] is None


def test_list_reports_the_three_states(app):
    client, _, _ = app
    listing = client.get("/api/projects/shop-api/principles").json()
    states = {item["key"]: item["state"] for item in listing["principles"]}
    assert set(states.values()) == {MISSING}

    client.put("/api/projects/shop-api/principles/security", json={"content": RULES})
    states = {item["key"]: item["state"] for item in client.get("/api/projects/shop-api/principles").json()["principles"]}
    assert states["security"] == DRAFT
    assert states["business"] == MISSING

    client.post("/api/projects/shop-api/principles/security/approve")
    states = {item["key"]: item["state"] for item in client.get("/api/projects/shop-api/principles").json()["principles"]}
    assert states["security"] == APPROVED


def test_unapprove_returns_the_principle_to_draft(app):
    client, registry, _ = app
    client.put("/api/projects/shop-api/principles/security", json={"content": RULES})
    client.post("/api/projects/shop-api/principles/security/approve")
    response = client.post("/api/projects/shop-api/principles/security/unapprove")
    assert response.status_code == 200
    assert response.json()["state"] == DRAFT
    assert registry.principle_records("shop-api")["security"]["approved_commit"] is None


def test_unknown_principle_is_rejected(app):
    client, _, _ = app
    assert client.get("/api/projects/shop-api/principles/nope").status_code == 422
    assert client.put("/api/projects/shop-api/principles/nope", json={"content": "x"}).status_code == 422
    assert client.post("/api/projects/shop-api/principles/nope/approve").status_code == 422


def test_missing_project_returns_a_plain_error(app):
    client, _, _ = app
    response = client.get("/api/projects/ghost/principles")
    assert response.status_code == 404
    assert "does not exist" in response.json()["error"]


def test_principle_without_a_repository_reports_the_setup_step(app):
    client, registry, _ = app
    manifest = registry.manifest("shop-api")
    manifest["repo"]["url"] = ""
    registry._write_manifest("shop-api", manifest)
    response = client.get("/api/projects/shop-api/principles")
    assert response.status_code == 200
    assert "repository URL" in response.json()["repo_error"]


def test_principle_without_a_deploy_key_reports_the_setup_step(app):
    client, registry, _ = app
    registry.secret_store.delete("project/shop-api", "deploy_key")
    registry.secret_store.delete("project/shop-api", "deploy_key.pub")
    manifest = registry.manifest("shop-api")
    manifest["repo"]["url"] = "https://github.com/example/shop-api"
    registry._write_manifest("shop-api", manifest)
    response = client.get("/api/projects/shop-api/principles")
    assert response.status_code == 200
    assert "deploy key" in response.json()["repo_error"]


def test_validation_helper_rejects_lowercase_and_empty_ids():
    with pytest.raises(Exception):
        validate_for_approval("# Security\n\n- sec-001: lowercase is not a rule id\n")
    with pytest.raises(Exception):
        validate_for_approval("# Security\n")
