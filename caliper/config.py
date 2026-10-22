"""Caliper runtime configuration.

Every value comes from a ``CALIPER_*`` environment variable. Secret-like
values also accept a ``<NAME>_FILE`` variant so Docker and Kubernetes can
mount them as files instead of putting them in the environment.

All writable state lives under ``CALIPER_DATA_DIR`` (default ``/data``):

    caliper.db      SQLite run state
    projects/       one directory per project (manifest + local clone)
    work/           one worktree directory per project/request
    secrets/        encrypted secret store + public keys + known_hosts
    models.yaml     non-secret model settings

Nothing else is written except the ephemeral runtime directory
(``CALIPER_RUNTIME_DIR``, default ``/run/caliper``) which must be a tmpfs in
the container. Materialized SSH private keys only ever exist there, with mode
0600, for the duration of one operation.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = "/data"
DEFAULT_RUNTIME_DIR = "/run/caliper"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
MASTER_KEY_BYTES = 32
INIT_SECRETS_COMMAND = "python -m caliper init-secrets"


class ConfigError(Exception):
    """Configuration is missing or invalid; the app must not start."""


def _env(environ: dict | None = None) -> dict:
    return dict(os.environ if environ is None else environ)


def read_secret_value(name: str, environ: dict | None = None) -> str | None:
    """Read a secret-like setting, preferring ``<NAME>_FILE`` over ``<NAME>``.

    Returns ``None`` when neither is set. An explicitly configured but
    unreadable file is an error, never a silent fallback to the environment.
    """
    env = _env(environ)
    file_value = env.get(f"{name}_FILE")
    if file_value:
        path = Path(file_value)
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(
                f"{name}_FILE points at {path}, but the file cannot be read: {exc.strerror or exc}. "
                "Fix the mount or unset the variable."
            ) from exc
    value = env.get(name)
    return value.strip() if value is not None else None


def decode_master_key(value: str) -> bytes:
    """Decode the master key from base64 (preferred), hex, or 32 raw bytes."""
    text = (value or "").strip()
    if not text:
        raise ConfigError(
            "The Caliper master key is empty. Create one with:\n"
            f"  {INIT_SECRETS_COMMAND} --output /data/secrets/master.key\n"
            "Then start Caliper with CALIPER_MASTER_KEY_FILE=/data/secrets/master.key "
            "(or CALIPER_MASTER_KEY set to the same value)."
        )
    candidates: list[bytes] = []
    padded = text + "=" * (-len(text) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            candidates.append(decoder(padded))
        except (binascii.Error, ValueError):
            pass
    if len(text) == 64:
        try:
            candidates.append(bytes.fromhex(text))
        except ValueError:
            pass
    if len(text.encode("utf-8")) == MASTER_KEY_BYTES:
        candidates.append(text.encode("utf-8"))
    valid = [candidate for candidate in candidates if len(candidate) == MASTER_KEY_BYTES]
    if not valid:
        raise ConfigError(
            "The Caliper master key is invalid: it must be 32 bytes, written as base64 "
            "(preferred), 64 hex characters, or 32 raw bytes. "
            f"Create a valid key with: {INIT_SECRETS_COMMAND} --output <path>"
        )
    return valid[0]


def load_master_key(environ: dict | None = None) -> bytes:
    value = read_secret_value("CALIPER_MASTER_KEY", environ)
    if value is None:
        raise ConfigError(
            "Caliper cannot start: no master key is configured. The encrypted secret "
            "store needs one before any project or model key can be read.\n"
            f"Create one with:\n  {INIT_SECRETS_COMMAND} --output /data/secrets/master.key\n"
            "Then start Caliper with:\n"
            "  CALIPER_MASTER_KEY_FILE=/data/secrets/master.key python -m caliper\n"
            "In Docker, pass the same file as a Compose/Kubernetes secret. Keep it outside "
            "CALIPER_DATA_DIR so a backup of the data cannot decrypt itself."
        )
    return decode_master_key(value)


def init_master_key(path: Path, force: bool = False) -> Path:
    """Generate a master key and write it atomically with mode 0600."""
    path = Path(path)
    if path.exists() and not force:
        raise ConfigError(
            f"A master key already exists at {path}. Refusing to overwrite it, because that "
            "would make every secret encrypted with the old key unreadable. "
            "Use --force only if you have verified that no data needs the old key."
        )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, stat.S_IRWXU)
    except OSError:
        pass
    value = base64.urlsafe_b64encode(secrets.token_bytes(MASTER_KEY_BYTES)).decode("ascii")
    fd, temporary = tempfile.mkstemp(prefix=".master-key-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (value + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise
    os.chmod(path, 0o600)
    return path


@dataclass(frozen=True)
class CaliperConfig:
    data_dir: Path
    db_path: Path
    projects_root: Path
    work_root: Path
    secrets_dir: Path
    models_config: Path
    runtime_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    log_level: str = "INFO"
    shutdown_grace_seconds: float = 10.0

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "CaliperConfig":
        env = _env(environ)
        data_dir = Path(env.get("CALIPER_DATA_DIR") or DEFAULT_DATA_DIR)
        db_path = Path(env.get("CALIPER_DB_PATH") or data_dir / "caliper.db")
        projects_root = Path(env.get("CALIPER_PROJECTS_ROOT") or data_dir / "projects")
        work_root = Path(env.get("CALIPER_WORK_ROOT") or data_dir / "work")
        secrets_dir = Path(env.get("CALIPER_SECRETS_DIR") or data_dir / "secrets")
        models_config = Path(env.get("CALIPER_MODELS_CONFIG") or data_dir / "models.yaml")
        runtime_dir = Path(env.get("CALIPER_RUNTIME_DIR") or DEFAULT_RUNTIME_DIR)
        host = env.get("CALIPER_HOST") or DEFAULT_HOST
        try:
            port = int(env.get("CALIPER_PORT") or DEFAULT_PORT)
        except ValueError:
            raise ConfigError("CALIPER_PORT must be a number.")
        log_level = (env.get("CALIPER_LOG_LEVEL") or "INFO").upper()
        try:
            shutdown_grace = float(env.get("CALIPER_SHUTDOWN_GRACE_SECONDS") or 10)
        except ValueError:
            raise ConfigError("CALIPER_SHUTDOWN_GRACE_SECONDS must be a number of seconds.")
        return cls(
            data_dir=data_dir,
            db_path=db_path,
            projects_root=projects_root,
            work_root=work_root,
            secrets_dir=secrets_dir,
            models_config=models_config,
            runtime_dir=runtime_dir,
            host=host,
            port=port,
            log_level=log_level,
            shutdown_grace_seconds=shutdown_grace,
        )

    @property
    def secret_store_path(self) -> Path:
        return self.secrets_dir / "store.sqlite3"

    @property
    def master_key_path(self) -> Path:
        return self.secrets_dir / "master.key"

    def ensure_directories(self) -> None:
        """Create the writable layout. Safe to call repeatedly."""
        for directory, mode in (
            (self.data_dir, 0o750),
            (self.projects_root, 0o750),
            (self.work_root, 0o750),
            (self.secrets_dir, 0o700),
            (self.runtime_dir, 0o700),
        ):
            try:
                directory.mkdir(parents=True, exist_ok=True, mode=mode)
            except OSError as exc:
                raise ConfigError(
                    f"Cannot create {directory}: {exc.strerror or exc}. "
                    "Mount CALIPER_DATA_DIR and CALIPER_RUNTIME_DIR as writable volumes "
                    "(the root filesystem is read-only in the container)."
                ) from exc

    def load_master_key(self, environ: dict | None = None) -> bytes:
        return load_master_key(environ)

    def legacy_db_path(self) -> Path:
        """Where Phase 0c kept SQLite when this data directory was the repo root."""
        return self.data_dir / ".caliper-state" / "state.sqlite3"

    def legacy_secrets_dir(self) -> Path:
        """Where Phase 0c kept plaintext keys when this data directory was the repo root."""
        return self.data_dir / ".caliper-state" / "secrets"


def check_writable(directory: Path) -> tuple[bool, str]:
    """Write a tiny temporary file to prove the directory is writable."""
    directory = Path(directory)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".caliper-write-", dir=str(directory))
    except OSError as exc:
        return False, f"{directory} is not writable: {exc.strerror or exc}"
    try:
        os.write(fd, b"ok")
        os.fsync(fd)
    except OSError as exc:
        return False, f"{directory} is not writable: {exc.strerror or exc}"
    finally:
        os.close(fd)
        try:
            os.unlink(name)
        except OSError:
            pass
    return True, f"{directory} is writable"
