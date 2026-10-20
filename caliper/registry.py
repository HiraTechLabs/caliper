import datetime
import re
from pathlib import Path

import yaml

from caliper.principles import PRINCIPLES, render_principle_file

PRINCIPLE_ORDER = ["security", "business", "architecture", "techstack", "cleancode", "performance"]

STACK_PRESETS = {"python-fastapi": "Python + FastAPI"}


class ProjectError(Exception):
    pass


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def manifest_defaults(id: str, name: str, repo_mode: str, repo_url: str | None, stack_preset: str, product: str | None) -> dict:
    return {
        "id": id,
        "name": name,
        "created_at": _now(),
        "stack_preset": stack_preset,
        "repo": {"mode": repo_mode, "url": repo_url},
        "product": product,
        "environments": {
            "dev": {"host": "", "deploy": "docker compose up -d"},
            "prod": {"host": "", "deploy": "docker compose up -d"},
        },
    }


class ProjectRegistry:
    def __init__(self, projects_root: Path):
        self.projects_root = Path(projects_root)
        self.projects_root.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project_id):
            raise ProjectError("Project id must be lowercase letters, digits or dashes.")
        return self.projects_root / project_id

    def slugify(self, name: str) -> str:
        slug = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")
        return slug or "project"

    def list_projects(self) -> list[dict]:
        manifests = []
        for dir in sorted(self.projects_root.iterdir()):
            manifest_path = dir / "manifest.yaml"
            if dir.is_dir() and manifest_path.is_file():
                manifests.append(yaml.safe_load(manifest_path.read_text(encoding="utf-8")))
        return manifests

    def get_project(self, project_id: str) -> dict:
        dir = self._project_dir(project_id)
        manifest_path = dir / "manifest.yaml"
        if not manifest_path.is_file():
            raise ProjectError(f"Project '{project_id}' does not exist.")
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["principles"] = self._load_principles(dir)
        return manifest

    def _load_principles(self, dir: Path) -> list[dict]:
        loaded = []
        for key in PRINCIPLE_ORDER:
            path = dir / "principles" / f"{key}.md"
            loaded.append({"key": key, "title": PRINCIPLES[key]["title"], "content": path.read_text(encoding="utf-8")})
        return loaded

    def create_project(self, name: str, repo_mode: str, repo_url: str | None = None,
                       stack_preset: str = "python-fastapi", product: str | None = None) -> dict:
        if not name or not name.strip():
            raise ProjectError("Project name is required.")
        if repo_mode not in ("create_new", "existing"):
            raise ProjectError("Repository mode must be 'create_new' or 'existing'.")
        if stack_preset not in STACK_PRESETS:
            raise ProjectError(f"Unknown stack preset '{stack_preset}'.")
        if repo_mode == "existing" and not (repo_url and repo_url.strip()):
            raise ProjectError("Repository URL is required when using an existing repository.")

        project_id = self.slugify(name)
        dir = self._project_dir(project_id)
        if (dir / "manifest.yaml").is_file():
            raise ProjectError(f"Project '{project_id}' already exists.")

        manifest = manifest_defaults(
            id=project_id, name=name.strip(), repo_mode=repo_mode,
            repo_url=repo_url.strip() if repo_url and repo_url.strip() else None,
            stack_preset=stack_preset, product=product,
        )
        dir.mkdir(parents=True, exist_ok=False)
        (dir / "principles").mkdir()
        for key in PRINCIPLE_ORDER:
            (dir / "principles" / f"{key}.md").write_text(render_principle_file(key), encoding="utf-8")
        (dir / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        return manifest
