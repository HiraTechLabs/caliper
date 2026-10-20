import subprocess
from pathlib import Path

import yaml

from caliper.principles import PRINCIPLE_ORDER, PRINCIPLE_PREFIXES


def make_git_repo(path: Path, branch: str = "main") -> str:
    """Create a local bare repository with one commit; returns its file:// URL."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "--bare", f"--initial-branch={branch}", str(path)],
        check=True, capture_output=True,
    )
    work = path.parent / (path.name + "-work")
    subprocess.run(
        ["git", "init", "-q", f"--initial-branch={branch}", str(work)],
        check=True, capture_output=True,
    )
    (work / "README.md").write_text("# project\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "-c", "user.name=Caliper", "-c", "user.email=caliper@localhost",
         "commit", "-qm", "init"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "push", "-q", str(path), branch], check=True, capture_output=True
    )
    return path.as_uri()


def approve_principles(registry, project_id: str) -> None:
    """Mark the six principles as approved (two rules each) without touching Git.

    The rules of a real project come from the repository; this shortcut is for the
    tests that only need the workflow to have approved rule IDs.
    """
    path = registry.projects_root / project_id / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    principles = manifest.setdefault("principles", {})
    for key in PRINCIPLE_ORDER:
        principles[key] = {
            "state": "approved",
            "content_hash": f"hash-{key}",
            "approved_commit": f"commit-{key}",
            "approved_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "rule_ids": [f"{PRINCIPLE_PREFIXES[key]}-001", f"{PRINCIPLE_PREFIXES[key]}-002"],
        }
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
