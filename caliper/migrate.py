"""One-way migration from the Phase 0c on-disk layout.

Phase 0c wrote SQLite to ``<repo>/.caliper-state/state.sqlite3`` and kept
plaintext SSH and model keys under ``<repo>/.caliper-state/secrets``. Phase 1
keeps all state under ``CALIPER_DATA_DIR`` and encrypts every secret.

The migration is deliberately conservative:

* the legacy database is **copied**, never moved, so a failed migration still
  has the only valid copy;
* every plaintext secret is written to the encrypted store and read back
  before its file is overwritten and removed;
* an existing but different encrypted value is never overwritten (that would
  silently lose the value the running Caliper already uses);
* running the migration again is safe.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

import yaml

from caliper.config import CaliperConfig
from caliper.models_config import ROLE_ORDER
from caliper.secret_files import secure_unlink
from caliper.secrets import SecretNotFound, SecretStore, SecretStoreError

PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ENVIRONMENTS = ("dev", "prod")


class MigrationError(Exception):
    """A legacy item could not be migrated safely; nothing was destroyed for it."""


def _copy_database(config: CaliperConfig, logger: logging.Logger) -> bool:
    legacy = config.legacy_db_path()
    target = config.db_path
    if not legacy.is_file() or target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, temporary = tempfile.mkstemp(prefix=".caliper-db-", dir=str(target.parent))
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        shutil.copy2(legacy, temporary_path)
        os.chmod(temporary_path, 0o600)
        connection = sqlite3.connect(str(temporary_path))
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        if not result or result[0] != "ok":
            raise MigrationError(
                f"The legacy database {legacy} failed SQLite integrity_check; it was not copied."
            )
        os.replace(temporary_path, target)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    logger.info("Migrated SQLite run state from %s to %s (legacy copy kept).", legacy, target)
    return True


def _put_verified(store: SecretStore, scope: str, name: str, value: str) -> None:
    try:
        existing = store.get(scope, name)
    except SecretNotFound:
        existing = None
    if existing is not None:
        if existing != value:
            raise MigrationError(
                f"{scope}/{name} already exists in the encrypted store with a different value. "
                "Caliper will not overwrite it; compare the two keys and remove the stale one manually."
            )
        return
    store.put(scope, name, value)
    if store.get(scope, name) != value:
        raise MigrationError(f"{scope}/{name} could not be read back after migration.")


def _migrate_pair(store: SecretStore, scope: str, name: str, private_path: Path,
                  public_path: Path | None, failures: list[str], logger: logging.Logger) -> None:
    if not private_path.is_file():
        return
    try:
        private_value = private_path.read_text(encoding="utf-8")
        _put_verified(store, scope, name, private_value)
        if public_path is not None and public_path.is_file():
            _put_verified(store, scope, name + ".pub", public_path.read_text(encoding="utf-8"))
    except (OSError, SecretStoreError, MigrationError) as exc:
        failures.append(f"{private_path}: {exc}")
        return
    # Verified: the encrypted copy is readable. Now destroy the plaintext copies.
    secure_unlink(private_path)
    if public_path is not None:
        secure_unlink(public_path)
    logger.info("Migrated plaintext key %s into the encrypted store.", private_path)


def _candidate_legacy_roots(config: CaliperConfig) -> list[Path]:
    roots = [config.legacy_secrets_dir()]
    # A custom CALIPER_SECRETS_DIR may still contain Phase 0c files.
    if config.secrets_dir != config.legacy_secrets_dir():
        roots.append(config.secrets_dir)
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def migrate_plaintext_secrets(config: CaliperConfig, store: SecretStore,
                              logger: logging.Logger | None = None) -> list[str]:
    logger = logger or logging.getLogger("caliper.migration")
    failures: list[str] = []

    for root in _candidate_legacy_roots(config):
        for role in ROLE_ORDER:
            _migrate_pair(
                store, f"model/{role}", "api_key", root / f"{role}.key", None,
                failures, logger,
            )
        projects = root / "projects"
        if not projects.is_dir():
            continue
        for project_dir in sorted(projects.iterdir()):
            if not project_dir.is_dir() or not PROJECT_ID_RE.fullmatch(project_dir.name):
                continue
            project_id = project_dir.name
            _migrate_pair(
                store, f"project/{project_id}", "deploy_key",
                project_dir / "repo_deploy", project_dir / "repo_deploy.pub",
                failures, logger,
            )
            for env in ENVIRONMENTS:
                _migrate_pair(
                    store, f"env/{project_id}/{env}", "ssh_key",
                    project_dir / f"env_{env}", project_dir / f"env_{env}.pub",
                    failures, logger,
                )
                _migrate_known_hosts(config, project_id, env, project_dir, failures, logger)

    if failures:
        raise MigrationError(
            "Some legacy secrets could not be migrated. Nothing was deleted for those items:\n- "
            + "\n- ".join(failures)
        )
    return failures


def _migrate_known_hosts(config: CaliperConfig, project_id: str, env: str,
                         legacy_project_dir: Path, failures: list[str],
                         logger: logging.Logger) -> None:
    source = legacy_project_dir / f"known_hosts_{env}"
    target = config.secrets_dir / "projects" / project_id / f"known_hosts_{env}"
    if not source.is_file() or source == target:
        return
    if target.is_file():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source, target)
        os.chmod(target, 0o600)
        source.unlink()
    except OSError as exc:
        failures.append(f"{source}: {exc}")


def migrate_models_config(config: CaliperConfig, logger: logging.Logger) -> bool:
    """Copy configured non-secret model settings from the packaged file once."""
    target = config.models_config
    if target.is_file():
        return False
    packaged = Path(__file__).resolve().parent / "models.yaml"
    if not packaged.is_file():
        return False
    try:
        data = yaml.safe_load(packaged.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return False
    roles = data if isinstance(data, dict) else {}
    if not any((roles.get(role) or {}).get("model") for role in ROLE_ORDER):
        return False
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, temporary = tempfile.mkstemp(prefix=".models-", dir=str(target.parent))
    try:
        os.write(fd, yaml.safe_dump(data, sort_keys=False).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(temporary, 0o640)
    os.replace(temporary, target)
    logger.info("Copied model settings to %s.", target)
    return True


def migrate_legacy(config: CaliperConfig, store: SecretStore,
                   logger: logging.Logger | None = None) -> dict:
    """Run every migration step. Raises MigrationError on an unsafe situation."""
    logger = logger or logging.getLogger("caliper.migration")
    report = {"database": False, "models_config": False, "secrets": True}
    report["database"] = _copy_database(config, logger)
    report["models_config"] = migrate_models_config(config, logger)
    migrate_plaintext_secrets(config, store, logger)
    return report
