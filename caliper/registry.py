"""Project registry: manifest, deploy/environment keys, principle records, connection tests."""

import datetime
import os
import re
from pathlib import Path

import yaml

from caliper.principles import (
    APPROVED,
    PRINCIPLE_ORDER,
    PRINCIPLE_TITLES,
    derive_state,
    empty_record,
    principle_path,
)
from caliper.remote import (
    RemoteError,
    generate_ed25519_key,
    repo_keys_url,
    ssh_copy_id_command,
    test_environment,
    test_repository,
)

ENVIRONMENT_ORDER = ("dev", "prod")

ENVIRONMENT_DEFAULTS = {
    "host": "",
    "user": "",
    "deploy_path": "",
    "compose_file": "docker-compose.yml",
    "health_url": "",
    "verified": False,
    "verified_at": None,
    "test_message": "",
    "key_created_at": None,
    "host_key_fingerprint": None,
    "host_key_pinned_at": None,
}


class ProjectError(Exception):
    pass


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def manifest_defaults(id: str, name: str, repo_url: str, product: str | None) -> dict:
    return {
        "id": id,
        "name": name,
        "created_at": _now(),
        "repo": {
            "url": repo_url,
            "verified": False,
            "verified_at": None,
            "test_message": "",
            "key_created_at": None,
            "default_branch": None,
        },
        "product": product,
        "principles": {key: empty_record() for key in PRINCIPLE_ORDER},
        "environments": {
            env: dict(ENVIRONMENT_DEFAULTS) for env in ENVIRONMENT_ORDER
        },
    }


class ProjectRegistry:
    def __init__(
        self,
        projects_root: Path,
        secrets_dir: Path | None = None,
        key_generator=None,
        repo_tester=None,
        env_tester=None,
    ):
        self.projects_root = Path(projects_root)
        self.projects_root.mkdir(parents=True, exist_ok=True)
        default_secrets = self.projects_root.parent / ".caliper-state" / "secrets"
        self.secrets_root = Path(secrets_dir or os.environ.get("CALIPER_SECRETS_DIR", default_secrets))
        self._key_generator = key_generator or generate_ed25519_key
        self._repo_tester = repo_tester or test_repository
        self._env_tester = env_tester or test_environment

    def _project_dir(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project_id):
            raise ProjectError("Project id must be lowercase letters, digits or dashes.")
        return self.projects_root / project_id

    def project_path(self, project_id: str) -> Path:
        return self._project_dir(project_id)

    def slugify(self, name: str) -> str:
        slug = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")
        return slug or "project"

    def _manifest_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "manifest.yaml"

    def manifest(self, project_id: str) -> dict:
        """The stored manifest (project metadata) without derived data."""
        return self._read_manifest(project_id)

    def _read_manifest(self, project_id: str) -> dict:
        path = self._manifest_path(project_id)
        if not path.is_file():
            raise ProjectError(f"Project '{project_id}' does not exist.")
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        repo = manifest.setdefault("repo", {})
        repo.setdefault("url", None)
        repo.setdefault("verified", False)
        repo.setdefault("verified_at", None)
        repo.setdefault("test_message", "")
        repo.setdefault("key_created_at", None)
        repo.setdefault("default_branch", None)
        legacy = manifest.pop("principles_status", None) or {}
        principles = manifest.setdefault("principles", {})
        for key in PRINCIPLE_ORDER:
            record = principles.get(key) or {}
            for field, default in empty_record().items():
                record.setdefault(field, default)
            if legacy.get(key) == "approved" and not record.get("approved_commit"):
                # An approval without a recorded content hash and commit cannot stay approved.
                record["state"] = "draft"
            principles[key] = record
        environments = manifest.setdefault("environments", {})
        for env in ENVIRONMENT_ORDER:
            entry = environments.setdefault(env, {})
            for key, value in ENVIRONMENT_DEFAULTS.items():
                entry.setdefault(key, value)
        return manifest

    def _write_manifest(self, project_id: str, manifest: dict) -> None:
        self._manifest_path(project_id).write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )

    def list_projects(self) -> list[dict]:
        manifests = []
        for dir in sorted(self.projects_root.iterdir()):
            manifest_path = dir / "manifest.yaml"
            if dir.is_dir() and manifest_path.is_file():
                manifests.append(self._read_manifest(dir.name))
        return manifests

    def get_project(self, project_id: str) -> dict:
        manifest = self._read_manifest(project_id)
        records = manifest.get("principles") or {}
        manifest["principles_records"] = records
        manifest["principles"] = self._load_principles(project_id, records)
        repo = manifest.get("repo") or {}
        repo["keys_url"] = repo_keys_url(repo.get("url") or "")
        repo["public_key"] = self.public_deploy_key(project_id)
        manifest["environment_keys"] = {
            env: self.public_environment_key(project_id, env) for env in ENVIRONMENT_ORDER
        }
        return manifest

    def _cached_principle_content(self, project_id: str, key: str) -> str | None:
        """Principle content from the local clone of the repository (no network)."""
        path = self._project_dir(project_id) / "repo" / principle_path(key)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def _load_principles(self, project_id: str, records: dict) -> list[dict]:
        loaded = []
        for key in PRINCIPLE_ORDER:
            record = records.get(key) or {}
            content = self._cached_principle_content(project_id, key)
            loaded.append({
                "key": key,
                "title": PRINCIPLE_TITLES[key],
                "content": content or "",
                "status": derive_state(record, content),
            })
        return loaded

    def principle_records(self, project_id: str) -> dict:
        return self._read_manifest(project_id).get("principles") or {}

    def set_principle_record(self, project_id: str, key: str, record: dict) -> None:
        manifest = self._read_manifest(project_id)
        manifest.setdefault("principles", {})[key] = record
        self._write_manifest(project_id, manifest)

    def set_principle_records(self, project_id: str, records: dict) -> None:
        manifest = self._read_manifest(project_id)
        principles = manifest.setdefault("principles", {})
        for key, record in records.items():
            principles[key] = record
        self._write_manifest(project_id, manifest)

    def approved_rule_ids(self, project_id: str) -> list[str]:
        """Rule IDs of the approved principles of a project."""
        records = self.principle_records(project_id)
        ids: list[str] = []
        for key in PRINCIPLE_ORDER:
            record = records.get(key) or {}
            if record.get("state") != APPROVED:
                continue
            for rule_id in record.get("rule_ids") or []:
                if rule_id not in ids:
                    ids.append(rule_id)
        return ids

    def create_project(self, name: str, repo_url: str, product: str | None = None) -> dict:
        if not name or not name.strip():
            raise ProjectError("Project name is required.")
        if not repo_url or not repo_url.strip():
            raise ProjectError("Repository URL is required.")

        project_id = self.slugify(name)
        dir = self._project_dir(project_id)
        if (dir / "manifest.yaml").is_file():
            raise ProjectError(f"Project '{project_id}' already exists.")

        manifest = manifest_defaults(
            id=project_id, name=name.strip(), repo_url=repo_url.strip(), product=product,
        )
        dir.mkdir(parents=True, exist_ok=False)
        self._write_manifest(project_id, manifest)
        return manifest

    def deploy_key_path(self, project_id: str) -> Path:
        return self.secrets_root / "projects" / project_id / "repo_deploy"

    def environment_key_path(self, project_id: str, env: str) -> Path:
        if env not in ENVIRONMENT_ORDER:
            raise ProjectError(f"Unknown environment '{env}'.")
        return self.secrets_root / "projects" / project_id / f"env_{env}"

    def known_hosts_path(self, project_id: str, env: str) -> Path:
        if env not in ENVIRONMENT_ORDER:
            raise ProjectError(f"Unknown environment '{env}'.")
        return self.secrets_root / "projects" / project_id / f"known_hosts_{env}"

    def public_deploy_key(self, project_id: str) -> str:
        path = self.deploy_key_path(project_id).with_name("repo_deploy.pub")
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""

    def public_environment_key(self, project_id: str, env: str) -> str:
        path = self.environment_key_path(project_id, env).with_name(f"env_{env}.pub")
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""

    def ensure_deploy_key(self, project_id: str) -> str:
        manifest = self._read_manifest(project_id)
        path = self.deploy_key_path(project_id)
        if not (path.is_file() and path.with_name("repo_deploy.pub").is_file()):
            try:
                self._key_generator(path, f"caliper-{project_id}")
            except RemoteError as exc:
                raise ProjectError(str(exc))
            manifest["repo"]["key_created_at"] = _now()
            manifest["repo"]["verified"] = False
            manifest["repo"]["test_message"] = (
                "Key generated. Add the public key to the repository, then test read/write access."
            )
            self._write_manifest(project_id, manifest)
        return self.public_deploy_key(project_id)

    def ensure_environment_key(self, project_id: str, env: str) -> str:
        path = self.environment_key_path(project_id, env)
        if not (path.is_file() and path.with_name(f"env_{env}.pub").is_file()):
            try:
                self._key_generator(path, f"caliper-{project_id}-{env}")
            except RemoteError as exc:
                raise ProjectError(str(exc))
            manifest = self._read_manifest(project_id)
            manifest["environments"][env]["key_created_at"] = _now()
            self._write_manifest(project_id, manifest)
        return self.public_environment_key(project_id, env)

    def save_environment(self, project_id: str, env: str, fields: dict) -> dict:
        if env not in ENVIRONMENT_ORDER:
            raise ProjectError(f"Unknown environment '{env}'.")
        manifest = self._read_manifest(project_id)
        entry = manifest["environments"][env]
        values = {}
        for key in ("host", "user", "deploy_path", "compose_file", "health_url"):
            values[key] = str(fields.get(key, "") or "").strip()
        if not values["host"] or not values["user"]:
            raise ProjectError("Host and user are required.")
        if not values["deploy_path"] or not values["compose_file"]:
            raise ProjectError("Deploy path and compose file are required.")
        if not values["health_url"]:
            raise ProjectError("Health URL is required.")
        changed = any(entry.get(key) != value for key, value in values.items())
        host_changed = values["host"] != (entry.get("host") or "")
        entry.update(values)
        if changed:
            entry["verified"] = False
            entry["verified_at"] = None
            entry["test_message"] = ""
        if host_changed:
            # A different machine has a different host key: the pinned one no longer applies.
            entry["host_key_fingerprint"] = None
            entry["host_key_pinned_at"] = None
            known_hosts = self.known_hosts_path(project_id, env)
            if known_hosts.is_file():
                known_hosts.unlink()
        self._write_manifest(project_id, manifest)
        self.ensure_environment_key(project_id, env)
        return entry

    def environment_status(self, project_id: str, env: str) -> dict:
        """Everything the API shows for one environment: key, command, fingerprint, state."""
        if env not in ENVIRONMENT_ORDER:
            raise ProjectError(f"Unknown environment '{env}'.")
        entry = self._read_manifest(project_id)["environments"][env]
        key_path = self.environment_key_path(project_id, env)
        public_key = self.public_environment_key(project_id, env)
        host = entry.get("host") or ""
        user = entry.get("user") or ""
        return {
            "env": env,
            "host": host,
            "user": user,
            "deploy_path": entry.get("deploy_path"),
            "compose_file": entry.get("compose_file"),
            "health_url": entry.get("health_url"),
            "verified": bool(entry.get("verified")),
            "verified_at": entry.get("verified_at"),
            "test_message": entry.get("test_message"),
            "public_key": public_key,
            "key_path": str(key_path),
            "key_exists": key_path.is_file(),
            "key_mode": self._key_mode(key_path),
            "ssh_copy_id_command": (
                ssh_copy_id_command(key_path, user, host) if public_key and host and user else None
            ),
            "host_key_fingerprint": entry.get("host_key_fingerprint"),
            "host_key_pinned_at": entry.get("host_key_pinned_at"),
            "known_hosts_path": str(self.known_hosts_path(project_id, env)),
        }

    @staticmethod
    def _key_mode(path: Path) -> str | None:
        if not path.is_file():
            return None
        return oct(path.stat().st_mode & 0o777)

    def repository_status(self, project_id: str) -> dict:
        """Everything the API shows for the repository access of a project."""
        manifest = self._read_manifest(project_id)
        repo = manifest["repo"]
        key_path = self.deploy_key_path(project_id)
        return {
            "url": repo.get("url"),
            "mode": repo.get("mode"),
            "public_key": self.public_deploy_key(project_id),
            "key_path": str(key_path),
            "key_exists": key_path.is_file(),
            "key_mode": self._key_mode(key_path),
            "keys_url": repo_keys_url(repo.get("url") or ""),
            "verified": bool(repo.get("verified")),
            "verified_at": repo.get("verified_at"),
            "test_message": repo.get("test_message"),
            "key_created_at": repo.get("key_created_at"),
            "default_branch": repo.get("default_branch"),
        }

    def test_repository_access(self, project_id: str) -> tuple[bool, str]:
        manifest = self._read_manifest(project_id)
        repo = manifest["repo"]
        if not repo.get("url"):
            raise ProjectError("Add a repository URL first.")
        try:
            self.ensure_deploy_key(project_id)
            message = self._repo_tester(repo["url"], self.deploy_key_path(project_id))
            repo["verified"] = True
            repo["test_message"] = message
            repo["verified_at"] = _now()
            self._write_manifest(project_id, manifest)
            return True, message
        except (RemoteError, ProjectError) as exc:
            repo["verified"] = False
            repo["verified_at"] = None
            repo["test_message"] = str(exc)
            self._write_manifest(project_id, manifest)
            return False, str(exc)

    def test_environment_access(self, project_id: str, env: str) -> tuple[bool, str]:
        if env not in ENVIRONMENT_ORDER:
            raise ProjectError(f"Unknown environment '{env}'.")
        manifest = self._read_manifest(project_id)
        entry = manifest["environments"][env]
        for key, label in (("host", "host"), ("user", "user"), ("deploy_path", "deploy path"),
                           ("compose_file", "compose file"), ("health_url", "health URL")):
            if not entry.get(key):
                raise ProjectError(f"Save the {label} first.")
        try:
            self.ensure_environment_key(project_id, env)
            result = self._env_tester(
                entry["host"],
                entry["user"],
                self.environment_key_path(project_id, env),
                deploy_path=entry["deploy_path"],
                compose_file=entry["compose_file"],
                known_hosts_path=self.known_hosts_path(project_id, env),
            )
            message, fingerprint = self._read_tester_result(result)
            entry["verified"] = True
            entry["test_message"] = message
            entry["verified_at"] = _now()
            if fingerprint:
                entry["host_key_fingerprint"] = fingerprint
                entry["host_key_pinned_at"] = _now()
            self._write_manifest(project_id, manifest)
            return True, message
        except (RemoteError, ProjectError) as exc:
            entry["verified"] = False
            entry["verified_at"] = None
            entry["test_message"] = str(exc)
            self._write_manifest(project_id, manifest)
            return False, str(exc)

    def setup_status(self, manifest: dict) -> list[dict]:
        repo = manifest.get("repo") or {}
        records = manifest.get("principles_records") or {}
        environments = manifest.get("environments") or {}
        approved = sum(1 for key in PRINCIPLE_ORDER if (records.get(key) or {}).get("state") == APPROVED)
        repo_ready = bool(repo.get("url")) and bool(repo.get("verified"))
        principles_ready = approved == len(PRINCIPLE_ORDER)
        items = [
            {
                "key": "repository",
                "label": "Repository access",
                "ready": repo_ready,
                "reason": "Ready." if repo_ready else (
                    "Add a repository URL." if not repo.get("url")
                    else "Generate the deploy key and test read/write access."
                ),
            },
            {
                "key": "principles",
                "label": "Principles",
                "ready": principles_ready,
                "reason": "All 6 approved." if principles_ready else f"{approved} of {len(PRINCIPLE_ORDER)} approved.",
            },
        ]
        for env in ENVIRONMENT_ORDER:
            entry = environments.get(env) or {}
            ready = all(entry.get(key) for key in (
                "host", "user", "deploy_path", "compose_file", "health_url",
            )) and bool(entry.get("verified"))
            items.append({
                "key": env,
                "label": f"{env.capitalize()} environment",
                "ready": ready,
                "reason": "Ready." if ready else "Configure the fields and test the connection.",
            })
        return items

    def setup_missing(self, manifest: dict) -> list[str]:
        return [item["label"] for item in self.setup_status(manifest) if not item["ready"]]

    def setup_complete(self, manifest: dict) -> bool:
        return not self.setup_missing(manifest)

    @staticmethod
    def _read_tester_result(result) -> tuple[str, str | None]:
        if isinstance(result, dict):
            return str(result.get("message") or ""), result.get("host_key_fingerprint")
        return str(result), None
