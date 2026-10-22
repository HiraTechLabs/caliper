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
            "graph": None,
            "graph_text": None,
            "graph_error": None,
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
        return {
            "project_id": project_id,
            "branch": branch,
            "commit": None,
            "synced": synced,
            "error": " ".join(errors) or None,
            "manifest": manifest,
            "manifest_text": manifest_text,
            "graph": graph,
            "graph_text": graph_text,
            "graph_error": graph_error,
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
