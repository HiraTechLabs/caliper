"""Encrypted secret store (AES-256-GCM).

Values are encrypted before they reach SQLite, with a fresh random 96-bit
nonce per write. The scope and name are authenticated as associated data, so
a ciphertext cannot be moved to another scope/name without failing to decrypt.
Only names are ever returned in bulk; getting a value always decrypts exactly
one record.

The master key is supplied by :mod:`caliper.config` and never stored in the
database. A wrong key or tampered ciphertext raises a plain error that names
the scope/name and never the value.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import threading
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
MAX_VALUE_BYTES = 256 * 1024
SCOPE_RE = re.compile(
    r"^(?:project/[a-z0-9][a-z0-9-]*|env/[a-z0-9][a-z0-9-]*/(?:dev|prod)|model/(?:planning|execution|review))$"
)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
    scope TEXT NOT NULL,
    name TEXT NOT NULL,
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, name)
);
CREATE TABLE IF NOT EXISTS store_meta (
    key TEXT PRIMARY KEY,
    nonce BLOB NOT NULL,
    ciphertext BLOB NOT NULL
);
"""

KEY_CHECK = "master_key_check"
KEY_CHECK_PLAINTEXT = b"caliper-store-v1"
KEY_CHECK_AAD = b"caliper:store:v1:key-check"


class SecretStoreError(Exception):
    """A secret could not be stored or read. Messages never contain values."""


class SecretStoreLocked(SecretStoreError):
    """The master key is absent or invalid, so the store cannot be used."""


class SecretNotFound(SecretStoreError):
    """No secret exists under this scope and name."""


def validate_scope(scope: str) -> str:
    scope = str(scope or "").strip()
    if not SCOPE_RE.fullmatch(scope):
        raise SecretStoreError(
            f"Invalid secret scope {scope!r}. Use project/<id>, env/<project>/<env> "
            "with env dev|prod, or model/<planning|execution|review>."
        )
    return scope


def validate_name(name: str) -> str:
    name = str(name or "").strip()
    if not NAME_RE.fullmatch(name):
        raise SecretStoreError(
            f"Invalid secret name {name!r}. Use letters, digits, dot, dash or underscore."
        )
    return name


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


class SecretStore:
    """One encrypted store, unlocked with a 32-byte master key."""

    def __init__(self, path: Path, key: bytes | None):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._unlocked_reason = ""
        if key is None:
            self._key = None
            self._conn = None
            self._unlocked_reason = "No master key is configured."
            return
        if not isinstance(key, (bytes, bytearray)) or len(key) != 32:
            self._key = None
            self._conn = None
            self._unlocked_reason = "The master key is not 32 bytes."
            return
        self._key = bytes(key)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA secure_delete=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        if not self._verify_key_check():
            return
        self._unlocked_reason = "Secret store is unlocked."

    def _verify_key_check(self) -> bool:
        """Refuse a well-formed but wrong key before readiness says green.

        A fresh store records a fixed plaintext encrypted with the supplied
        key. An existing store must decrypt that value with the same key.
        The check value is not a secret and never contains user data.
        """
        row = self._conn.execute(
            "SELECT nonce, ciphertext FROM store_meta WHERE key = ?", (KEY_CHECK,)
        ).fetchone()
        if row is None:
            nonce = secrets.token_bytes(NONCE_BYTES)
            ciphertext = AESGCM(self._key).encrypt(nonce, KEY_CHECK_PLAINTEXT, KEY_CHECK_AAD)
            self._conn.execute(
                "INSERT INTO store_meta (key, nonce, ciphertext) VALUES (?,?,?)",
                (KEY_CHECK, nonce, ciphertext),
            )
            self._conn.commit()
            return True
        try:
            AESGCM(self._key).decrypt(row["nonce"], row["ciphertext"], KEY_CHECK_AAD)
        except InvalidTag:
            self._conn.close()
            self._key = None
            self._conn = None
            self._unlocked_reason = (
                "The master key does not match this secret store. Restore the key that "
                "created it, or point CALIPER_DATA_DIR at the matching data; the store "
                "was not modified."
            )
            return False
        return True

    # --- state ---------------------------------------------------------------
    @property
    def unlocked(self) -> bool:
        return self._key is not None and self._conn is not None

    @property
    def locked_reason(self) -> str:
        return self._unlocked_reason

    def _require_unlocked(self) -> None:
        if not self.unlocked:
            raise SecretStoreLocked(
                f"The secret store is locked: {self._unlocked_reason} "
                "Create a master key with `python -m caliper init-secrets --output <path>` "
                "and set CALIPER_MASTER_KEY_FILE."
            )

    def close(self) -> None:
        if self._conn is not None:
            with self._lock:
                self._conn.close()
                self._conn = None

    # --- crypto --------------------------------------------------------------
    @staticmethod
    def _aad(scope: str, name: str) -> bytes:
        return f"caliper:secret:v1:{scope}:{name}".encode("utf-8")

    def _encrypt(self, scope: str, name: str, value: str) -> tuple[bytes, bytes]:
        nonce = secrets.token_bytes(NONCE_BYTES)
        ciphertext = AESGCM(self._key).encrypt(
            nonce, value.encode("utf-8"), self._aad(scope, name)
        )
        return nonce, ciphertext

    def _decrypt(self, scope: str, name: str, nonce: bytes, ciphertext: bytes) -> str:
        try:
            plaintext = AESGCM(self._key).decrypt(nonce, ciphertext, self._aad(scope, name))
        except InvalidTag:
            raise SecretStoreError(
                f"Secret {scope}/{name} could not be decrypted. The master key is wrong or "
                "the stored ciphertext was modified. Restore the original master key; "
                "Caliper will not guess or overwrite the value."
            )
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError:
            raise SecretStoreError(
                f"Secret {scope}/{name} is not valid UTF-8. The ciphertext is damaged."
            )

    # --- operations ----------------------------------------------------------
    def put(self, scope: str, name: str, value: str) -> None:
        self._require_unlocked()
        scope = validate_scope(scope)
        name = validate_name(name)
        if not isinstance(value, str):
            raise SecretStoreError("Secret values must be text.")
        encoded = value.encode("utf-8")
        if not encoded:
            raise SecretStoreError("Secret values may not be empty.")
        if len(encoded) > MAX_VALUE_BYTES:
            raise SecretStoreError(
                f"Secret {scope}/{name} is larger than {MAX_VALUE_BYTES} bytes."
            )
        nonce, ciphertext = self._encrypt(scope, name, value)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO secrets (scope, name, nonce, ciphertext, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(scope, name) DO UPDATE SET "
                "nonce=excluded.nonce, ciphertext=excluded.ciphertext, updated_at=excluded.updated_at",
                (scope, name, nonce, ciphertext, now, now),
            )
            self._conn.commit()

    def get(self, scope: str, name: str) -> str:
        self._require_unlocked()
        scope = validate_scope(scope)
        name = validate_name(name)
        with self._lock:
            row = self._conn.execute(
                "SELECT nonce, ciphertext FROM secrets WHERE scope = ? AND name = ?",
                (scope, name),
            ).fetchone()
        if row is None:
            raise SecretNotFound(f"Secret {scope}/{name} does not exist.")
        return self._decrypt(scope, name, row["nonce"], row["ciphertext"])

    def exists(self, scope: str, name: str) -> bool:
        self._require_unlocked()
        scope = validate_scope(scope)
        name = validate_name(name)
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM secrets WHERE scope = ? AND name = ?", (scope, name)
            ).fetchone()
        return row is not None

    def delete(self, scope: str, name: str) -> bool:
        self._require_unlocked()
        scope = validate_scope(scope)
        name = validate_name(name)
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM secrets WHERE scope = ? AND name = ?", (scope, name)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def list(self, scope: str) -> list[str]:
        """Names in one scope. Values are never returned."""
        self._require_unlocked()
        scope = validate_scope(scope)
        with self._lock:
            rows = self._conn.execute(
                "SELECT name FROM secrets WHERE scope = ? ORDER BY name", (scope,)
            ).fetchall()
        return [row["name"] for row in rows]

    def delete_scope(self, scope: str) -> int:
        """Delete every secret in one scope. Returns the number removed."""
        self._require_unlocked()
        scope = validate_scope(scope)
        with self._lock:
            cursor = self._conn.execute("DELETE FROM secrets WHERE scope = ?", (scope,))
            self._conn.commit()
            return cursor.rowcount

    def scopes(self) -> list[str]:
        self._require_unlocked()
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT scope FROM secrets ORDER BY scope"
            ).fetchall()
        return [row["scope"] for row in rows]
