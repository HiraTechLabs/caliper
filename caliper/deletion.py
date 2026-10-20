import shutil
import threading
from pathlib import Path

from caliper.remote import stop_environment


class ProjectDeleteError(Exception):
    pass


class ProjectDeleter:
    def __init__(self, registry, requests, data_dir: Path | None = None, stopper=None):
        self.registry = registry
        self.requests = requests
        self.projects_root = Path(registry.projects_root).resolve()
        self.data_dir = Path(data_dir) if data_dir is not None else self.projects_root.parent
        self._stopper = stopper or stop_environment
        self._lock = threading.Lock()

    def delete(self, project_id: str, stop_dev: bool = False, stop_prod: bool = False) -> dict:
        with self._lock:
            project_dir = self._safe_project_path(project_id)
            secrets_dir = self._safe_secrets_path(project_id)
            manifest = self.registry.get_project(project_id)
            previous = self.requests.cancel_active_for_project(project_id)
            stopped = []
            try:
                if stop_dev:
                    self._stop(project_id, manifest, "dev")
                    stopped.append("dev")
                if stop_prod:
                    self._stop(project_id, manifest, "prod")
                    stopped.append("prod")
            except ProjectDeleteError as exc:
                self.requests.restore_requests(previous)
                raise ProjectDeleteError(f"{exc} Nothing was deleted.") from exc

            try:
                self._remove_secrets(secrets_dir)
                self._remove_local(project_dir)
                details = {
                    "removed": {
                        "requests": len(self.requests.list_for_project(project_id)),
                        "cancelled_requests": [req["id"] for req in previous],
                        "secrets": True,
                        "local_path": str(project_dir),
                        "deployments_stopped": stopped,
                    },
                    "kept": {
                        "github_repository": manifest["repo"].get("url"),
                        "caliper_files": ".caliper/ files in the repository",
                    },
                }
                audit_id = self.requests.delete_project_with_audit(project_id, manifest["name"], details)
            except Exception as exc:
                self.requests.restore_requests(previous)
                raise ProjectDeleteError(f"Could not delete project data: {exc}") from exc
            return {
                "deleted": True,
                "project_id": project_id,
                "project_name": manifest["name"],
                "requests": details["removed"]["requests"],
                "cancelled_requests": details["removed"]["cancelled_requests"],
                "deployments_stopped": stopped,
                "audit_id": audit_id,
            }

    def _storage_root(self) -> Path:
        expected = (self.data_dir / "projects").resolve()
        if self.projects_root != expected:
            raise ProjectDeleteError(
                "Project storage does not match CALIPER_DATA_DIR/projects; refusing to delete."
            )
        return expected

    def _safe_project_path(self, project_id: str) -> Path:
        root = self._storage_root()
        raw = self.registry.project_path(project_id)
        if raw.is_symlink():
            raise ProjectDeleteError("Refusing to delete: the project directory is a symlink.")
        resolved = raw.resolve()
        project_root = (root / project_id).resolve()
        if resolved != project_root or root not in resolved.parents:
            raise ProjectDeleteError("Refusing to delete: the project path is outside CALIPER_DATA_DIR/projects.")
        return raw

    def _remove_local(self, project_dir: Path) -> None:
        if project_dir.exists():
            shutil.rmtree(project_dir)

    def _safe_secrets_path(self, project_id: str) -> Path:
        root = (Path(self.registry.secrets_root) / "projects").resolve()
        raw = root / project_id
        if not raw.exists() and not raw.is_symlink():
            return raw
        if raw.is_symlink():
            raise ProjectDeleteError("Refusing to delete: the project secrets directory is a symlink.")
        resolved = raw.resolve()
        if resolved != (root / project_id).resolve() or root not in resolved.parents:
            raise ProjectDeleteError("Refusing to delete: the project secrets path is outside CALIPER_SECRETS_DIR.")
        return raw

    def _remove_secrets(self, secrets_dir: Path) -> None:
        if secrets_dir.exists():
            shutil.rmtree(secrets_dir)

    def _stop(self, project_id: str, manifest: dict, env: str) -> None:
        entry = manifest["environments"][env]
        missing = [
            label for key, label in (
                ("host", "host"), ("user", "user"), ("deploy_path", "deploy path"),
                ("compose_file", "compose file"),
            )
            if not entry.get(key)
        ]
        if missing:
            raise ProjectDeleteError(
                f"The {env.capitalize()} environment is not configured ({', '.join(missing)} missing)."
            )
        key_path = self.registry.environment_key_path(project_id, env)
        if not key_path.is_file():
            raise ProjectDeleteError(
                f"The {env.capitalize()} environment SSH key is missing."
            )
        try:
            self._stopper(
                entry["host"], entry["user"], entry["deploy_path"],
                entry["compose_file"], key_path,
                self.registry.known_hosts_path(project_id, env),
            )
        except Exception as exc:
            raise ProjectDeleteError(f"Could not stop {env.capitalize()}: {exc}") from exc
