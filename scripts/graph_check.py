#!/usr/bin/env python3
"""graph-check: the component graph and the decision log agree with the repository.

Checks:
- the graph parses, every node has a unique id and a path, every edge names nodes
  that exist, and no node path points outside the repository;
- every component path exists in the checkout, except a runtime path ignored by Git
  (for example ``projects/``);
- every Python module under ``caliper/`` is represented by a graph node;
- every row of the decision index has exactly one entry and ids are contiguous;
- with ``--base REF``: when code changed since REF, the decision log and the graph
  changed in the same range.

Usage:
    python scripts/graph_check.py [--graph PATH] [--decisions PATH] [--base REF]
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GRAPH = "docs/graph.yaml"
DEFAULT_DECISIONS = "docs/DECISIONS.md"
NODE_KINDS = {
    "application",
    "domain",
    "infrastructure",
    "presentation",
    "data-store",
    "test-suite",
}
INDEX_ROW = re.compile(r"^\|\s*(D-\d{3})\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|(.+)\|(.+)\|$")
ENTRY_HEADING = re.compile(r"^###\s+(D-\d{3})\b")
CODE_CHANGE_PREFIXES = ("caliper/", "scripts/", "tests/")
CODE_CHANGE_FILES = {"pyproject.toml", "package.json", "package-lock.json"}


class CheckError(Exception):
    pass


def _relative(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        raise CheckError(f"graph path must be relative to the repository: {path}")
    resolved = (ROOT / candidate).resolve()
    if ROOT not in resolved.parents and resolved != ROOT:
        raise CheckError(f"graph path points outside the repository: {path}")
    return candidate


def _git_ignores(relative: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(relative)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def check_graph(graph_path: Path) -> dict:
    if not graph_path.is_file():
        raise CheckError(f"graph file not found: {graph_path.relative_to(ROOT)}")
    try:
        graph = yaml.safe_load(graph_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise CheckError(f"graph is not valid YAML: {exc}")

    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise CheckError("graph has no nodes list")
    if not isinstance(edges, list):
        raise CheckError("graph has no edges list")

    ids: list[str] = []
    paths: list[Path] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise CheckError(f"graph node is not a mapping: {node!r}")
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            raise CheckError(f"graph node without an id: {node!r}")
        if node_id in ids:
            raise CheckError(f"duplicate graph node id: {node_id}")
        ids.append(node_id)
        if node.get("kind") not in NODE_KINDS:
            raise CheckError(
                f"node {node_id}: kind must be one of {', '.join(sorted(NODE_KINDS))}"
            )
        if not str(node.get("description") or "").strip():
            raise CheckError(f"node {node_id}: description is required")
        node_path = _relative(str(node.get("path") or ""))
        if not str(node_path).strip():
            raise CheckError(f"node {node_id}: path is required")
        paths.append(node_path)
        if not (ROOT / node_path).exists() and not _git_ignores(node_path):
            raise CheckError(f"node {node_id}: path does not exist: {node_path}")

    seen_edges: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise CheckError(f"graph edge is not a mapping: {edge!r}")
        source = str(edge.get("from") or "").strip()
        target = str(edge.get("to") or "").strip()
        relation = str(edge.get("relation") or "").strip()
        if source not in ids:
            raise CheckError(f"edge references an unknown source node: {source!r}")
        if target not in ids:
            raise CheckError(f"edge references an unknown target node: {target!r}")
        if source == target:
            raise CheckError(f"edge points a node at itself: {source}")
        if not relation:
            raise CheckError(f"edge {source} -> {target} has no relation")
        if (source, target) in seen_edges:
            raise CheckError(f"duplicate edge: {source} -> {target}")
        seen_edges.add((source, target))

    covered: set[str] = set()
    for node_path in paths:
        if node_path.is_dir() or str(node_path).endswith("/"):
            covered.update(
                module.relative_to(ROOT).as_posix()
                for module in (ROOT / node_path).glob("**/*.py")
            )
        else:
            covered.add(node_path.as_posix())
    modules = {
        module.relative_to(ROOT).as_posix()
        for module in (ROOT / "caliper").glob("*.py")
        if module.name not in {"__init__.py", "__main__.py"}
    }
    missing = sorted(modules - covered)
    if missing:
        raise CheckError("code modules without a graph node: " + ", ".join(missing))

    return {"nodes": len(ids), "edges": len(edges)}


def check_decisions(decisions_path: Path) -> dict:
    if not decisions_path.is_file():
        raise CheckError(f"decision log not found: {decisions_path.relative_to(ROOT)}")
    text = decisions_path.read_text(encoding="utf-8")
    if "## Index" not in text or "## Entries" not in text:
        raise CheckError("decision log needs '## Index' and '## Entries' sections")
    index_text = text.split("## Index", 1)[1].split("## Entries", 1)[0]
    entries_text = text.split("## Entries", 1)[1]

    index_ids: list[str] = []
    for line in index_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if line.startswith("| id ") or set(line) <= set("|- "):
            continue
        match = INDEX_ROW.match(line)
        if not match:
            raise CheckError(f"malformed decision index row: {line}")
        decision_id, date_text = match.group(1), match.group(2)
        try:
            datetime.date.fromisoformat(date_text)
        except ValueError:
            raise CheckError(f"{decision_id}: index date is not ISO: {date_text}")
        if decision_id in index_ids:
            raise CheckError(f"duplicate decision index id: {decision_id}")
        index_ids.append(decision_id)

    entry_ids = [match.group(1) for match in map(ENTRY_HEADING.match, entries_text.splitlines()) if match]
    if len(entry_ids) != len(set(entry_ids)):
        raise CheckError("duplicate decision entry id")
    if set(index_ids) != set(entry_ids):
        missing = sorted(set(index_ids) - set(entry_ids))
        extra = sorted(set(entry_ids) - set(index_ids))
        detail = []
        if missing:
            detail.append("index rows without an entry: " + ", ".join(missing))
        if extra:
            detail.append("entries without an index row: " + ", ".join(extra))
        raise CheckError("; ".join(detail))
    expected = [f"D-{number:03d}" for number in range(1, len(index_ids) + 1)]
    if index_ids != expected:
        raise CheckError("decision ids must be contiguous from D-001 in index order")
    return {"decisions": len(index_ids)}


def _changed_paths(base: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base, "--", "."],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise CheckError(f"cannot compare with {base}: {exc}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def check_changes(base: str, graph_path: Path, decisions_path: Path) -> None:
    changed = _changed_paths(base)
    code = [
        path
        for path in changed
        if path.startswith(CODE_CHANGE_PREFIXES) or path in CODE_CHANGE_FILES
    ]
    if not code:
        return
    relative_graph = graph_path.relative_to(ROOT).as_posix()
    relative_decisions = decisions_path.relative_to(ROOT).as_posix()
    if relative_decisions not in changed:
        raise CheckError(f"code changed since {base} but {relative_decisions} was not updated")
    if relative_graph not in changed:
        raise CheckError(f"code changed since {base} but {relative_graph} was not updated")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graph-check", description=__doc__)
    parser.add_argument("--graph", default=DEFAULT_GRAPH)
    parser.add_argument("--decisions", default=DEFAULT_DECISIONS)
    parser.add_argument("--base", default=None)
    args = parser.parse_args(argv)
    graph_path = (ROOT / args.graph).resolve()
    decisions_path = (ROOT / args.decisions).resolve()
    try:
        graph_result = check_graph(graph_path)
        decisions_result = check_decisions(decisions_path)
        if args.base:
            check_changes(args.base, graph_path, decisions_path)
    except CheckError as exc:
        print(f"graph-check: FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        f"graph-check: OK: {graph_result['nodes']} nodes, {graph_result['edges']} edges, "
        f"{decisions_result['decisions']} decisions"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
