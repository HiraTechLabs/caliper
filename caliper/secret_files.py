"""Short-lived plaintext files for tools that require a path.

SSH and Git accept a private key only as a file. Caliper writes the decrypted
key into the ephemeral runtime directory (a tmpfs in the container), mode 0600,
for exactly one operation, then overwrites and removes it even when the
operation fails. It is never written under CALIPER_DATA_DIR.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from caliper.secrets import SecretStore, SecretStoreError


def write_private_file(directory: Path, value: str, suffix: str = "") -> Path:
    directory = Path(directory)
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
    except OSError as exc:
        raise SecretStoreError(
            f"The runtime directory {directory} is not writable: {exc.strerror or exc}. "
            "Mount CALIPER_RUNTIME_DIR as a tmpfs (for example /run/caliper)."
        ) from exc
    try:
        fd, name = tempfile.mkstemp(prefix="caliper-secret-", suffix=suffix, dir=str(directory))
    except OSError as exc:
        raise SecretStoreError(
            f"Cannot materialize a secret in {directory}: {exc.strerror or exc}"
        ) from exc
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, value.encode("utf-8"))
        os.fsync(fd)
    except OSError as exc:
        os.close(fd)
        secure_unlink(Path(name))
        raise SecretStoreError(
            f"Cannot materialize a secret in {directory}: {exc.strerror or exc}"
        ) from exc
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return Path(name)


def secure_unlink(path: Path) -> None:
    """Overwrite a regular file with zeros, then unlink it. Never raises."""
    path = Path(path)
    try:
        if path.is_symlink():
            path.unlink(missing_ok=True)
            return
        if path.is_file():
            try:
                size = path.stat().st_size
                fd = os.open(path, os.O_WRONLY)
                try:
                    os.write(fd, b"\0" * size)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                pass
            path.unlink(missing_ok=True)
    except OSError:
        pass


@contextmanager
def materialize(store: SecretStore, scope: str, name: str, runtime_dir: Path,
                suffix: str = ".key"):
    """Yield a 0600 file holding one secret, then remove it on every path."""
    value = store.get(scope, name)
    path = write_private_file(runtime_dir, value, suffix=suffix)
    try:
        yield path
    finally:
        secure_unlink(path)
