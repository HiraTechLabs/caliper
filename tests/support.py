import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from caliper.principles import PRINCIPLE_ORDER, PRINCIPLE_PREFIXES
from caliper.registry import ENVIRONMENT_ORDER

requires_ssh_keygen = pytest.mark.skipif(
    os.name == "nt" or shutil.which("ssh-keygen") is None,
    reason="ssh-keygen is not available; this test exercises real key generation",
)
requires_ssh_keyscan = pytest.mark.skipif(
    shutil.which("ssh-keyscan") is None,
    reason="ssh-keyscan is not available; this test exercises a real host-key scan",
)


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
        ["git", "-C", str(work), "remote", "add", "origin", str(path)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(work), "push", "-q", "origin", branch], check=True, capture_output=True
    )
    return path.as_uri()


def _approved_principles(manifest: dict) -> None:
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


def approve_principles(registry, project_id: str) -> None:
    """Mark the six principles as approved without touching Git."""
    path = registry.projects_root / project_id / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    _approved_principles(manifest)
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def complete_setup(registry, project_id: str) -> None:
    """Mark a project as fully set up, including verified environments."""
    path = registry.projects_root / project_id / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest["repo"]["url"] = manifest["repo"].get("url") or f"https://github.com/example/{project_id}"
    manifest["repo"]["verified"] = True
    manifest["repo"]["test_message"] = "Read and write access verified."
    _approved_principles(manifest)
    for env in ENVIRONMENT_ORDER:
        manifest["environments"][env].update({
            "host": f"{env}.example.com",
            "user": "deploy",
            "deploy_path": "/srv/app",
            "compose_file": "docker-compose.yml",
            "health_url": f"https://{env}.example.com/health",
            "verified": True,
            "test_message": "SSH and docker compose are reachable.",
            "host_key_fingerprint": "SHA256:pinned",
        })
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
