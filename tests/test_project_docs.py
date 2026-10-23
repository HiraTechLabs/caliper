import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.project_docs import ProjectDocsService
from caliper.registry import ProjectRegistry
from caliper.state import RequestStore
from tests.support import make_git_repo


def _key_generator(path: Path, comment: str) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("PRIVATE KEY", encoding="utf-8")
    public = path.with_name(path.name + ".pub")
    public.write_text(f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI{comment}", encoding="utf-8")
    return public.read_text(encoding="utf-8")


@pytest.fixture()
def app(tmp_path):
    bare = tmp_path / "remote" / "shop-api.git"
    url = make_git_repo(bare)
    registry = ProjectRegistry(
        tmp_path / "projects", secrets_dir=tmp_path / "secrets", key_generator=_key_generator
    )
    registry.create_project(name="Shop API", repo_url=url)
    registry.ensure_deploy_key("shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    client = TestClient(create_app(registry=registry, requests=requests))
    return client, registry, bare


def _remote_file(bare: Path, path: str) -> str:
    result = subprocess.run(
        ["git", "--git-dir", str(bare), "show", f"main:{path}"],
        capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _write_remote(bare: Path, files: dict[str, str], message: str) -> None:
    work = bare.parent / (bare.name + "-work")
    subprocess.run(["git", "-C", str(work), "fetch", "origin", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "reset", "--hard", "origin/main"], check=True, capture_output=True)
    for relative, content in files.items():
        path = work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "-c", "user.name=Human", "-c", "user.email=human@example.com",
         "commit", "-qm", message],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True, capture_output=True)


def test_init_writes_the_three_project_docs(app):
    client, _, bare = app

    response = client.post("/api/projects/shop-api/docs/init")

    assert response.status_code == 200
    body = response.json()
    assert body["initialized"] is True
    assert body["manifest"]["id"] == "shop-api"
    assert body["graph"] == {"nodes": [], "edges": []}
    assert body["decisions"] == []
    assert "id: shop-api" in _remote_file(bare, ".caliper/manifest.yaml")
    assert "nodes: []" in _remote_file(bare, ".caliper/graph.yaml")
    assert "Append one Markdown file" in _remote_file(bare, ".caliper/decisions/README.md")


def test_read_cached_returns_manifest_graph_and_decisions(app):
    client, _, _ = app
    client.post("/api/projects/shop-api/docs/init")

    response = client.get("/api/projects/shop-api/docs")

    assert response.status_code == 200
    body = response.json()
    assert body["synced"] is False
    assert ".caliper/manifest.yaml" not in body["missing"]
    assert body["graph_text"].startswith("# Project architecture graph")
    assert body["manifest_text"]


def test_sync_reads_an_external_graph_and_decision(app):
    client, _, bare = app
    client.post("/api/projects/shop-api/docs/init")
    _write_remote(bare, {
        ".caliper/graph.yaml": "nodes:\n  - id: api\n    path: src/api.py\nedges: []\n",
        ".caliper/decisions/2026-10-20-refunds.md": "# Refunds use a queue\n\n- Decision: queue\n",
    }, "add graph and decision")

    response = client.post("/api/projects/shop-api/docs/sync")

    assert response.status_code == 200
    body = response.json()
    assert body["synced"] is True
    assert body["graph"]["nodes"][0]["id"] == "api"
    assert body["decisions"][0]["title"] == "Refunds use a queue"


def test_append_decision_commits_one_file(app):
    client, registry, bare = app
    client.post("/api/projects/shop-api/docs/init")
    service = ProjectDocsService(registry)

    result = service.append_decision(
        "shop-api", "Use a queue", "Refunds go through a queue.", "Keeps the API responsive.",
        "Synchronous refunds.",
    )

    assert result["written"].endswith("-use-a-queue.md")
    assert "Refunds go through a queue." in _remote_file(bare, result["written"])
    assert any(item["title"] == "Use a queue" for item in result["decisions"])


def test_decision_api_appends_and_returns_the_new_list(app):
    client, _, bare = app
    client.post("/api/projects/shop-api/docs/init")

    response = client.post("/api/projects/shop-api/decisions", json={
        "title": "Use a queue",
        "decision": "Refunds go through a queue.",
        "reason": "Keeps the API responsive.",
        "alternatives": "Synchronous refunds.",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["written"].endswith("-use-a-queue.md")
    assert body["decisions"][0]["title"] == "Use a queue"
    assert "Refunds go through a queue." in _remote_file(bare, body["written"])


def test_decision_api_rejects_missing_fields(app):
    client, _, _ = app
    client.post("/api/projects/shop-api/docs/init")

    response = client.post("/api/projects/shop-api/decisions", json={"title": "Only a title"})

    assert response.status_code == 422
    assert "required" in response.json()["error"]


def test_form_decision_redirects_back_to_the_docs_section(app):
    client, _, bare = app
    client.post("/api/projects/shop-api/docs/init")

    response = client.post("/projects/shop-api/decisions", data={
        "title": "Use a queue",
        "decision": "Refunds go through a queue.",
        "reason": "Keeps the API responsive.",
        "alternatives": "",
    }, follow_redirects=False)

    assert response.status_code == 303
    assert "#docs" in response.headers["location"]
    assert "docs_message" in response.headers["location"]
    docs = client.get("/api/projects/shop-api/docs").json()
    assert docs["decisions"][0]["title"] == "Use a queue"
    assert "Refunds go through a queue." in _remote_file(
        bare, ".caliper/decisions/" + docs["decisions"][0]["name"]
    )


def test_invalid_graph_is_reported_without_a_server_error(app):
    client, _, bare = app
    client.post("/api/projects/shop-api/docs/init")
    _write_remote(bare, {
        ".caliper/graph.yaml": "nodes:\n  - id: api\n    path: src/api.py\nedges:\n  - from: api\n    to: db\n",
    }, "broken graph")

    response = client.post("/api/projects/shop-api/docs/sync")

    assert response.status_code == 200
    assert "missing node" in response.json()["graph_error"]


def test_docs_without_a_deploy_key_reports_the_setup_step(app):
    client, registry, _ = app
    registry.secret_store.delete("project/shop-api", "deploy_key")
    registry.secret_store.delete("project/shop-api", "deploy_key.pub")
    manifest = registry.manifest("shop-api")
    manifest["repo"]["url"] = "https://github.com/example/shop-api"
    registry._write_manifest("shop-api", manifest)

    response = client.post("/api/projects/shop-api/docs/init")

    assert response.status_code == 422
    assert "deploy key" in response.json()["error"]


def test_project_page_shows_the_docs_section(app):
    client, _, _ = app
    client.post("/api/projects/shop-api/docs/init")

    page = client.get("/projects/shop-api")

    assert page.status_code == 200
    assert "Knowledge workspace" in page.text
    assert ".caliper/graph.yaml" in page.text
    assert "Decisions" in page.text


def test_form_init_redirects_back_to_the_docs_section(app):
    client, _, _ = app

    response = client.post("/projects/shop-api/docs/init", follow_redirects=False)

    assert response.status_code == 303
    assert "#docs" in response.headers["location"]
    assert "docs_message" in response.headers["location"]
