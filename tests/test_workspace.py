"""The knowledge workspace: principles / manifest / decisions / graph views.

Page tests read served HTML only; no secret value is ever asserted into the
page, and the fixtures write obviously fake material into a local bare remote.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.principles import PrinciplesService
from caliper.project_docs import (
    decision_fields,
    manifest_rows,
    manifest_workspace,
    normalize_graph,
    redacted_manifest_text,
)
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


def _graph_payload(page) -> dict | None:
    match = re.search(
        r'<script id="caliper-graph-data" type="application/json">(.*?)</script>',
        page.text, re.S,
    )
    return json.loads(match.group(1)) if match else None


# --- graph normalization -----------------------------------------------------------

def test_normalize_graph_drops_edges_to_missing_nodes_and_warns():
    view = normalize_graph({
        "nodes": [{"id": "api", "path": "src/api.py"}],
        "edges": [{"from": "api", "to": "db"}],
    })

    assert [node["id"] for node in view["nodes"]] == ["api"]
    assert view["edges"] == []
    assert any("missing node db" in warning for warning in view["warnings"])
    assert view["counts"] == {"nodes": 1, "edges": 0}


def test_normalize_graph_keeps_the_first_of_duplicate_node_ids():
    view = normalize_graph({
        "nodes": [{"id": "api", "path": "src/api.py"}, {"id": "api", "path": "other/api.py"}],
        "edges": [],
    })

    assert [node["path"] for node in view["nodes"]] == ["src/api.py"]
    assert any("duplicate node id api" in warning for warning in view["warnings"])
    assert view["counts"] == {"nodes": 1, "edges": 0}


def test_normalize_graph_reports_structural_problems():
    wrong_type = normalize_graph({"nodes": "api", "edges": []})
    assert wrong_type["nodes"] == [] and wrong_type["edges"] == []
    assert wrong_type["warnings"] == ["graph.yaml: nodes and edges must be lists."]

    broken = normalize_graph({"nodes": [{"path": "no-id"}], "edges": []})
    assert broken["nodes"] == [] and broken["edges"] == []
    assert any("every node needs an id" in warning for warning in broken["warnings"])


# --- decision fields -----------------------------------------------------------------

def test_decision_fields_reads_the_recorded_fields():
    content = (
        "# Use a queue\n\n"
        "- Date: 2026-10-22T09:00:00\n"
        "- Decision: Refunds run through a queue.\n"
        "- Reason: Keeps the API responsive.\n"
        "- Alternatives rejected: synchronous refunds.\n"
    )

    assert decision_fields(content) == {
        "date": "2026-10-22T09:00:00",
        "decision": "Refunds run through a queue.",
        "reason": "Keeps the API responsive.",
        "alternatives": "synchronous refunds.",
    }


def test_decision_fields_ignores_unrelated_lines():
    assert decision_fields("Checkout works: fast\n- Unknown label: nope\n") == {
        "date": None, "decision": None, "reason": None, "alternatives": None,
    }


# --- manifest redaction ---------------------------------------------------------------

def test_redacted_manifest_text_hides_secret_values():
    redacted = redacted_manifest_text(
        "id: shop-api\n"
        "login:\n"
        "  password: hunter2\n"
        "  access_token: k8s-token-value\n"
        "users:\n"
        "  - name: ana\n"
        "  - link: k8s\n"
    )

    assert "hunter2" not in redacted
    assert "k8s-token-value" not in redacted
    assert "[redacted]" in redacted
    assert "ana" in redacted and "link: k8s" in redacted


def test_redacted_manifest_text_survives_invalid_yaml():
    redacted = redacted_manifest_text("{not valid yaml")
    assert redacted.startswith("[redacted]")
    assert "valid YAML" in redacted


def test_manifest_rows_flattens_nested_keys():
    rows = dict(manifest_rows({
        "name": "Shop API",
        "repo": {"url": "k8s", "api_key": "secret"},
        "links": ["a"],
        "nothing": None,
    }))

    assert rows == {
        "name": "Shop API",
        "repo.url": "k8s",
        "repo.api_key": "[redacted]",
        "links": "[a]",
        "nothing": "",
    }


def test_manifest_workspace_groups_the_sections():
    workspace = manifest_workspace({
        "id": "shop-api", "name": "Shop API", "product": "cos",
        "repo": {"url": "file://remote/shop-api.git", "access_token": "s"},
        "library": "sqlite",
    })

    assert [key for key, _ in workspace["project"]] == ["id", "name", "product"]
    assert ("access_token", "[redacted]") in workspace["repository"]
    assert ("url", "file://remote/shop-api.git") in workspace["repository"]
    assert ("library", "sqlite") in workspace["other"]


# --- project page views ---------------------------------------------------------------

def test_project_page_defaults_to_the_principles_view(app):
    client, _, _ = app

    page = client.get("/projects/shop-api")

    assert page.status_code == 200
    assert 'class="active" href="/projects/shop-api?view=principles#workspace"' in page.text
    assert 'class="active" href="/projects/shop-api?view=manifest' not in page.text

    # An unknown view falls back to the principles view.
    page = client.get("/projects/shop-api", params={"view": "bogus"})
    assert 'class="active" href="/projects/shop-api?view=principles#workspace"' in page.text


def test_principles_view_lists_the_rule_ids_from_the_repository(app):
    client, registry, bare = app
    client.post("/api/projects/shop-api/docs/init")
    _write_remote(bare, {
        ".caliper/principles/security.md": (
            "# Security\n\n"
            "- SEC-001 Never print private keys.\n"
            "- SEC-002 All traffic over SSH tunnels.\n"
        ),
    }, "write the security principle")
    PrinciplesService(registry).sync("shop-api")

    page = client.get("/projects/shop-api", params={"view": "principles"})

    assert page.status_code == 200
    assert "SEC-001" in page.text
    assert "SEC-002" in page.text
    assert "Never print private keys" in page.text


def test_manifest_view_renders_structured_rows_without_secrets(app):
    client, _, bare = app
    client.post("/api/projects/shop-api/docs/init")
    _write_remote(bare, {
        ".caliper/manifest.yaml": (
            "id: shop-api\n"
            "name: Shop API\n"
            "repo:\n"
            "  url: file:///remote/shop-api.git\n"
            "  access_token: super-secret-token-123\n"
            "settings:\n"
            "  registry_password: hunter2\n"
        ),
        ".caliper/graph.yaml": "nodes: []\nedges: []\n",
    }, "manifest with secrets")
    client.post("/api/projects/shop-api/docs/sync")

    page = client.get("/projects/shop-api", params={"view": "manifest"})

    assert page.status_code == 200
    # Structured rows: keys stay visible, secret-looking values do not.
    assert "access_token" in page.text
    assert "registry_password" in page.text
    assert "[redacted]" in page.text
    assert "super-secret-token-123" not in page.text
    assert "hunter2" not in page.text
    # The raw file view is redacted too.
    assert ".caliper/manifest.yaml" in page.text
    assert "Secret-looking values are redacted" in page.text


def test_decisions_view_lists_decisions_newest_first(app):
    client, _, _ = app
    client.post("/api/projects/shop-api/docs/init")
    client.post("/api/projects/shop-api/decisions", json={
        "title": "Use a queue", "decision": "Refunds run through a queue.",
        "reason": "Keeps the API responsive.",
    })
    client.post("/api/projects/shop-api/decisions", json={
        "title": "Deploy with Docker", "decision": "Ship Docker images.",
        "reason": "One artifact for Dev and Prod.",
    })

    page = client.get("/projects/shop-api", params={"view": "decisions"})

    assert page.status_code == 200
    assert "2 decisions" in page.text
    assert page.text.index("Deploy with Docker") < page.text.index("Use a queue")
    assert "Alternatives rejected" in page.text


def test_decisions_view_shows_an_empty_state(app):
    client, _, _ = app

    page = client.get("/projects/shop-api", params={"view": "decisions"})

    assert "No decisions recorded yet" in page.text
    assert ".caliper/decisions/" in page.text


def test_graph_view_embeds_the_normalized_payload(app):
    client, _, bare = app
    client.post("/api/projects/shop-api/docs/init")
    _write_remote(bare, {
        ".caliper/graph.yaml": (
            "nodes:\n"
            "  - id: api\n"
            "    title: Shop API\n"
            "    kind: service\n"
            "    path: src/api.py\n"
            "  - id: db\n"
            "    title: Database\n"
            "    kind: store\n"
            "edges:\n"
            "  - from: api\n"
            "    to: db\n"
            "    relation: reads\n"
        ),
    }, "add graph")
    client.post("/api/projects/shop-api/docs/sync")

    page = client.get("/projects/shop-api", params={"view": "graph"})

    assert page.status_code == 200
    payload = _graph_payload(page)
    assert payload["counts"] == {"nodes": 2, "edges": 1}
    assert [node["id"] for node in payload["nodes"]] == ["api", "db"]
    assert payload["nodes"][0]["title"] == "Shop API"
    assert payload["nodes"][0]["kind"] == "service"
    assert payload["edges"] == [{"from": "api", "to": "db", "relation": "reads"}]
    assert "Drag to pan" in page.text
    assert "data-graph-canvas" in page.text


def test_graph_view_warns_about_missing_node_references_but_draws_the_rest(app):
    client, _, bare = app
    client.post("/api/projects/shop-api/docs/init")
    _write_remote(bare, {
        ".caliper/graph.yaml": (
            "nodes:\n  - id: api\n    path: src/api.py\n"
            "edges:\n  - from: api\n    to: db\n"
        ),
    }, "graph with a broken edge")
    client.post("/api/projects/shop-api/docs/sync")

    page = client.get("/projects/shop-api", params={"view": "graph"})

    assert "missing node" in page.text
    payload = _graph_payload(page)
    assert payload["counts"]["nodes"] == 1
    assert payload["edges"] == []


def test_graph_view_reports_duplicate_node_ids(app):
    client, _, bare = app
    client.post("/api/projects/shop-api/docs/init")
    _write_remote(bare, {
        ".caliper/graph.yaml": (
            "nodes:\n"
            "  - id: api\n    path: src/api.py\n"
            "  - id: api\n    path: src/other.py\n"
            "edges: []\n"
        ),
    }, "graph with a duplicate node")
    client.post("/api/projects/shop-api/docs/sync")

    page = client.get("/projects/shop-api", params={"view": "graph"})

    assert "duplicate node id api" in page.text
    assert _graph_payload(page)["counts"] == {"nodes": 1, "edges": 0}


def test_graph_view_shows_an_empty_state_when_there_are_no_nodes(app):
    client, _, bare = app
    client.post("/api/projects/shop-api/docs/init")
    _write_remote(bare, {".caliper/graph.yaml": "nodes: []\nedges: []\n"}, "empty graph")
    client.post("/api/projects/shop-api/docs/sync")

    page = client.get("/projects/shop-api", params={"view": "graph"})

    assert "no drawable nodes yet" in page.text
    assert _graph_payload(page) is None


def test_graph_view_without_graph_yaml_shows_the_setup_state(app):
    client, _, _ = app

    page = client.get("/projects/shop-api", params={"view": "graph"})

    assert "Create the missing docs, then add nodes and edges" in page.text
    assert "data-graph-canvas" not in page.text
    assert _graph_payload(page) is None


def test_docs_forms_keep_the_active_view(app):
    client, _, bare = app
    client.post("/api/projects/shop-api/docs/init")

    response = client.post(
        "/projects/shop-api/docs/sync", data={"view": "manifest"}, follow_redirects=False
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert "view=manifest" in location
    assert "docs_message=" in location
    assert "#docs" in location
    assert location.count("?") == 1

    decision = client.post("/projects/shop-api/decisions", data={
        "title": "Use a queue",
        "decision": "Refunds run through a queue.",
        "reason": "Keeps the API responsive.",
        "view": "decisions",
    }, follow_redirects=False)
    assert decision.status_code == 303
    assert "view=decisions" in decision.headers["location"]
    assert "#docs" in decision.headers["location"]
    # The decision actually landed in the repository.
    docs = client.get("/api/projects/shop-api/docs").json()
    name = docs["decisions"][0]["name"]
    assert docs["decisions"][0]["title"] == "Use a queue"
    assert "Refunds run through a queue." in _remote_file(bare, f".caliper/decisions/{name}")
