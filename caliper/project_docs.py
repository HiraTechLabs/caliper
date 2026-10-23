"""Project repository docs: .caliper/manifest.yaml, graph.yaml and decisions/."""

import datetime
import re
from pathlib import Path

import yaml

from caliper.repo import GitRepo, RepoError

DOCS_DIR = ".caliper"
MANIFEST_PATH = f"{DOCS_DIR}/manifest.yaml"
GRAPH_PATH = f"{DOCS_DIR}/graph.yaml"
DECISIONS_DIR = f"{DOCS_DIR}/decisions"

DEFAULT_NODE_KIND = "component"
SENSITIVE_KEY = re.compile(
    r"(?i)(pass(word)?|secret|token|credential|private[_-]?key|api[_-]?key|"
    r"access[_-]?key|master[_-]?key)"
)
REDACTED = "[redacted]"


class ProjectDocsError(Exception):
    pass


def manifest_template(manifest: dict) -> str:
    data = {
        "id": manifest.get("id"),
        "name": manifest.get("name"),
        "product": manifest.get("product"),
        "repo": {"url": (manifest.get("repo") or {}).get("url")},
    }
    return yaml.safe_dump(data, sort_keys=False)


def graph_template() -> str:
    return (
        "# Project architecture graph. Add nodes and edges as the project grows.\n"
        "nodes: []\n"
        "edges: []\n"
    )


def decisions_readme() -> str:
    return (
        "# Decisions\n\n"
        "Append one Markdown file per decision. Never edit or delete an old decision.\n"
        "Each file should state the decision, its reason, and the alternatives rejected.\n"
    )


def normalize_graph(graph: dict) -> dict:
    """Turn the graph.yaml data into safe view data without inventing relations.

    Duplicate node ids keep only the first occurrence; edges that reference a
    missing (or dropped) node are dropped; every problem is reported as a warning.
    """
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return {
            "nodes": [], "edges": [], "warnings": ["graph.yaml: nodes and edges must be lists."],
            "counts": {"nodes": 0, "edges": 0},
        }
    warnings: list[str] = []
    node_ids: set[str] = set()
    ordered_ids: list[str] = []
    view_nodes: list[dict] = []
    for node in nodes:
        if not isinstance(node, dict) or not str(node.get("id") or "").strip():
            warnings.append("graph.yaml: every node needs an id.")
            continue
        node_id = str(node["id"]).strip()
        if node_id in node_ids:
            warnings.append(f"graph.yaml: duplicate node id {node_id}; keeping the first one.")
            continue
        node_ids.add(node_id)
        ordered_ids.append(node_id)
        view_nodes.append({
            "id": node_id,
            "title": str(node.get("title") or node_id),
            "kind": str(node.get("kind") or DEFAULT_NODE_KIND),
            "path": str(node.get("path") or "") or None,
            "description": str(node.get("description") or "") or None,
            "link": str(node.get("link") or "") or None,
        })
    view_edges: list[dict] = []
    for edge in edges:
        if not isinstance(edge, dict) or not str(edge.get("from") or "").strip() \
                or not str(edge.get("to") or "").strip():
            warnings.append("graph.yaml: every edge needs from and to.")
            continue
        source, target = str(edge["from"]).strip(), str(edge["to"]).strip()
        if source not in node_ids or target not in node_ids:
            missing = source if source not in node_ids else target
            warnings.append(f"graph.yaml: edge {source} -> {target} references a missing node {missing}.")
            continue
        view_edges.append({"from": source, "to": target, "relation": edge.get("relation")})
    return {
        "nodes": view_nodes,
        "edges": view_edges,
        "warnings": warnings,
        "counts": {"nodes": len(view_nodes), "edges": len(view_edges)},
    }


DECISION_FIELDS = {
    "Date": "date",
    "Decision": "decision",
    "Reason": "reason",
    "Alternatives rejected": "alternatives",
}


def decision_fields(content: str) -> dict:
    """Read the recorded fields of one decision file. Missing fields stay empty."""
    fields = {"date": None, "decision": None, "reason": None, "alternatives": None}
    for line in (content or "").splitlines():
        match = re.match(r"^\s*[-*]\s*([^:]{1,60}):\s*(.*)$", line)
        if not match:
            continue
        name = (match.group(1) or "").strip().rstrip("*")
        field = DECISION_FIELDS.get(name)
        if field and fields[field] is None:
            fields[field] = match.group(2).strip() or None
    return fields


def _redact(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if SENSITIVE_KEY.search(str(key)):
                redacted[key] = REDACTED
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def manifest_rows(manifest: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Flatten a (redacted) mapping into key/value rows for a structured view."""
    rows: list[tuple[str, str]] = []
    for key, value in (_redact(manifest) or {}).items():
        name = f"{prefix}{key}" if prefix else str(key)
        if isinstance(value, dict):
            rows.extend(manifest_rows(value, prefix=f"{name}."))
        elif isinstance(value, list):
            rows.append((name, yaml.safe_dump(value, default_flow_style=True).strip()))
        elif value is None:
            rows.append((name, ""))
        else:
            rows.append((name, str(value)))
    return rows


def manifest_workspace(manifest: dict) -> dict:
    """The .caliper/manifest.yaml as organized sections. Secret-looking values are redacted."""
    safe = _redact(manifest or {})
    top = ("id", "name", "product")
    project = {key: safe[key] for key in top if key in safe}
    repository = safe.get("repo") or {}
    other = {key: value for key, value in safe.items() if key not in top and key != "repo"}
    return {
        "project": manifest_rows(project),
        "repository": manifest_rows(repository),
        "other": manifest_rows(other),
    }


def redacted_manifest_text(text: str) -> str:
    """A YAML view of the manifest with secret-looking values replaced.

    The original text stays in Git; this is only what the UI may show.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return REDACTED + " (the manifest is not valid YAML)"
    if data is None:
        return "" if not text.strip() else REDACTED
    return yaml.safe_dump(_redact(data), sort_keys=False)


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower()).strip("-")
    return slug or "decision"


class ProjectDocsService:
    """Reads and writes the project docs that live in the project's Git repository."""

    def __init__(self, registry, repo_factory=None):
        self.registry = registry
        self._repo_factory = repo_factory or GitRepo

    def _repo(self, project_id: str) -> GitRepo:
        manifest = self.registry.manifest(project_id)
        url = (manifest.get("repo") or {}).get("url")
        if not url:
            raise ProjectDocsError(
                "Add a repository URL first. Project docs live in the repository."
            )
        key_exists = self.registry.deploy_key_exists(project_id)
        if not key_exists and not url.startswith("file://"):
            raise ProjectDocsError(
                "Generate the repository deploy key first, then test read/write access."
            )
        return self._repo_factory(
            self.registry.project_path(project_id) / "repo", url,
            key_context=None if url.startswith("file://")
            else (lambda: self.registry.deploy_key_context(project_id)),
        )

    def read_cached(self, project_id: str) -> dict:
        """Read the docs from the local clone without touching the network."""
        root = self.registry.project_path(project_id) / "repo"
        branch = (self.registry.manifest(project_id).get("repo") or {}).get("default_branch")
        if not (root / ".git").is_dir():
            return self._empty(project_id, branch, "No local clone yet. Sync from the repository first.")
        return self._read_root(project_id, root, branch=branch, synced=False)

    def sync(self, project_id: str) -> dict:
        """Fetch the default branch, then read the docs from it."""
        repo = self._repo(project_id)
        try:
            branch = repo.sync()
            commit = repo.head_commit()
        except RepoError as exc:
            raise ProjectDocsError(str(exc))
        result = self._read_root(project_id, repo.root, branch=branch, synced=True)
        result["commit"] = commit
        return result

    def ensure_scaffold(self, project_id: str) -> dict:
        """Create missing .caliper docs in the repository, never overwriting existing files."""
        repo = self._repo(project_id)
        try:
            repo.ensure_clone()
            branch = repo.sync()
        except RepoError as exc:
            raise ProjectDocsError(str(exc))
        manifest = self.registry.manifest(project_id)
        files = {}
        if not (repo.root / MANIFEST_PATH).is_file():
            files[MANIFEST_PATH] = manifest_template(manifest)
        if not (repo.root / GRAPH_PATH).is_file():
            files[GRAPH_PATH] = graph_template()
        if not (repo.root / DECISIONS_DIR).is_dir():
            files[f"{DECISIONS_DIR}/README.md"] = decisions_readme()
        try:
            commit = repo.write_many(files, "docs(caliper): initialize project docs") if files else repo.head_commit()
        except RepoError as exc:
            raise ProjectDocsError(str(exc))
        result = self._read_root(project_id, repo.root, branch=branch, synced=True)
        result["commit"] = commit
        result["initialized"] = bool(files)
        return result

    def append_decision(
        self,
        project_id: str,
        title: str,
        decision: str,
        reason: str,
        alternatives: str = "",
    ) -> dict:
        """Append one decision file and commit it to the default branch."""
        if not title.strip() or not decision.strip() or not reason.strip():
            raise ProjectDocsError("Title, decision and reason are required.")
        repo = self._repo(project_id)
        try:
            repo.ensure_clone()
            branch = repo.sync()
        except RepoError as exc:
            raise ProjectDocsError(str(exc))
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S-%f")
        name = f"{stamp}-{_slug(title)}.md"
        content = (
            f"# {title.strip()}\n\n"
            f"- Date: {_now()}\n"
            f"- Decision: {decision.strip()}\n"
            f"- Reason: {reason.strip()}\n"
            f"- Alternatives rejected: {alternatives.strip() or 'None recorded.'}\n"
        )
        try:
            commit = repo.write(f"{DECISIONS_DIR}/{name}", content, f"docs(decisions): {title.strip()}")
        except RepoError as exc:
            raise ProjectDocsError(str(exc))
        result = self._read_root(project_id, repo.root, branch=branch, synced=True)
        result["commit"] = commit
        result["written"] = f"{DECISIONS_DIR}/{name}"
        return result

    def _empty(self, project_id: str, branch: str | None, error: str) -> dict:
        return {
            "project_id": project_id,
            "branch": branch,
            "commit": None,
            "synced": False,
            "error": error,
            "manifest": None,
            "manifest_text": None,
            "manifest_text_redacted": None,
            "graph": None,
            "graph_text": None,
            "graph_error": None,
            "graph_view": {"nodes": [], "edges": [], "warnings": [], "counts": {"nodes": 0, "edges": 0}},
            "decisions": [],
            "decisions_error": None,
            "missing": [MANIFEST_PATH, GRAPH_PATH, DECISIONS_DIR],
            "initialized": False,
        }

    def _read_root(self, project_id: str, root: Path, branch: str | None, synced: bool) -> dict:
        manifest, manifest_text, manifest_error = self._read_yaml(root, MANIFEST_PATH)
        graph, graph_text, graph_error = self._read_yaml(root, GRAPH_PATH)
        if graph is not None:
            graph_error = self._validate_graph(graph) or graph_error
        decisions, decisions_error = self._read_decisions(root / DECISIONS_DIR)
        missing = [
            path for path in (MANIFEST_PATH, GRAPH_PATH, DECISIONS_DIR)
            if not (root / path).exists()
        ]
        errors = [error for error in (manifest_error, graph_error, decisions_error) if error]
        graph_view = normalize_graph(graph) if graph is not None else {
            "nodes": [], "edges": [], "warnings": [],
            "counts": {"nodes": 0, "edges": 0},
        }
        for decision in decisions:
            decision["fields"] = decision_fields(decision["content"])
        return {
            "project_id": project_id,
            "branch": branch,
            "commit": None,
            "synced": synced,
            "error": " ".join(errors) or None,
            "manifest": manifest,
            "manifest_text": manifest_text,
            "manifest_text_redacted": (
                redacted_manifest_text(manifest_text) if manifest_text else None
            ),
            "graph": graph,
            "graph_text": graph_text,
            "graph_error": graph_error,
            "graph_view": graph_view,
            "decisions": decisions,
            "decisions_error": decisions_error,
            "missing": missing,
            "initialized": False,
        }

    @staticmethod
    def _read_yaml(root: Path, relative: str) -> tuple[dict | None, str | None, str | None]:
        path = root / relative
        if not path.is_file():
            return None, None, None
        text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return None, text, f"{relative} is not valid YAML: {exc}"
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return None, text, f"{relative} must be a YAML mapping."
        return data, text, None

    @staticmethod
    def _validate_graph(graph: dict) -> str | None:
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        if not isinstance(nodes, list) or not isinstance(edges, list):
            return "graph.yaml: nodes and edges must be lists."
        ids = set()
        for node in nodes:
            if not isinstance(node, dict) or not node.get("id"):
                return "graph.yaml: every node needs an id."
            ids.add(node["id"])
        for edge in edges:
            if not isinstance(edge, dict) or not edge.get("from") or not edge.get("to"):
                return "graph.yaml: every edge needs from and to."
            if edge["from"] not in ids or edge["to"] not in ids:
                return f"graph.yaml: edge {edge['from']} -> {edge['to']} references a missing node."
        return None

    @staticmethod
    def _read_decisions(directory: Path) -> tuple[list[dict], str | None]:
        if not directory.is_dir():
            return [], None
        decisions = []
        try:
            for path in sorted(directory.glob("*.md")):
                if path.name.lower() == "readme.md":
                    continue
                content = path.read_text(encoding="utf-8")
                title = next(
                    (line[2:].strip() for line in content.splitlines() if line.startswith("# ")),
                    path.stem,
                )
                decisions.append({"name": path.name, "title": title, "content": content})
        except OSError as exc:
            return [], f"Cannot read .caliper/decisions: {exc}"
        return decisions, None
