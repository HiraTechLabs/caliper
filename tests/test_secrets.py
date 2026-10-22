"""AES-256-GCM secret store, scopes and short-lived key material (PLAN_P1 1.2)."""

import logging
import sqlite3
import stat
from pathlib import Path

import pytest

from caliper.logging_setup import JsonFormatter, redact, register_secret
from caliper.secret_files import materialize, secure_unlink, write_private_file
from caliper.secrets import (
    SecretNotFound,
    SecretStore,
    SecretStoreError,
    SecretStoreLocked,
    validate_name,
    validate_scope,
)

KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))
VALUE = "sk-super-secret-value-123"


@pytest.fixture()
def store(tmp_path):
    return SecretStore(tmp_path / "secrets" / "store.sqlite3", KEY)


def test_put_get_list_delete_round_trip(store):
    store.put("model/planning", "api_key", VALUE)

    assert store.get("model/planning", "api_key") == VALUE
    assert store.exists("model/planning", "api_key") is True
    assert store.list("model/planning") == ["api_key"]
    # List returns names only, never values.
    assert VALUE not in str(store.list("model/planning"))
    assert store.delete("model/planning", "api_key") is True
    assert store.delete("model/planning", "api_key") is False
    with pytest.raises(SecretNotFound):
        store.get("model/planning", "api_key")


def test_every_write_uses_a_fresh_nonce_and_ciphertext(store):
    store.put("project/shop", "deploy_key", VALUE)
    first = _row(store.path, "project/shop", "deploy_key")
    store.put("project/shop", "deploy_key", VALUE)
    second = _row(store.path, "project/shop", "deploy_key")

    assert first["nonce"] != second["nonce"]
    assert first["ciphertext"] != second["ciphertext"]
    assert store.get("project/shop", "deploy_key") == VALUE


def test_the_value_is_not_stored_in_plaintext_anywhere(store):
    store.put("model/review", "api_key", VALUE)

    for path in store.path.parent.iterdir():
        if path.is_file():
            assert VALUE.encode() not in path.read_bytes()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_a_wrong_master_key_fails_without_returning_the_value(tmp_path):
    path = tmp_path / "store.sqlite3"
    first = SecretStore(path, KEY)
    first.put("model/planning", "api_key", VALUE)
    first.close()

    second = SecretStore(path, OTHER_KEY)
    assert not second.unlocked
    with pytest.raises(SecretStoreError) as error:
        second.get("model/planning", "api_key")
    assert VALUE not in str(error.value)
    assert "does not match" in str(error.value)


def test_tampered_ciphertext_is_rejected(store):
    store.put("model/planning", "api_key", VALUE)
    connection = sqlite3.connect(str(store.path))
    connection.execute(
        "UPDATE secrets SET ciphertext = ? WHERE scope = ? AND name = ?",
        (b"tampered" + b"\0" * 16, "model/planning", "api_key"),
    )
    connection.commit()
    connection.close()

    with pytest.raises(SecretStoreError, match="could not be decrypted"):
        store.get("model/planning", "api_key")


def test_a_record_cannot_be_moved_to_another_scope(store):
    store.put("project/alpha", "deploy_key", VALUE)
    connection = sqlite3.connect(str(store.path))
    connection.execute(
        "UPDATE secrets SET scope = ? WHERE scope = ? AND name = ?",
        ("project/beta", "project/alpha", "deploy_key"),
    )
    connection.commit()
    connection.close()

    with pytest.raises(SecretStoreError, match="could not be decrypted"):
        store.get("project/beta", "deploy_key")


def test_scopes_and_names_are_validated():
    assert validate_scope("env/shop-api/prod") == "env/shop-api/prod"
    assert validate_scope("model/execution") == "model/execution"
    for bad in ("project/", "env/shop/qa", "model/other", "../model", "model/planning/x"):
        with pytest.raises(SecretStoreError):
            validate_scope(bad)
    assert validate_name("api_key") == "api_key"
    for bad in ("", "../key", "a" * 129):
        with pytest.raises(SecretStoreError):
            validate_name(bad)


def test_delete_scope_removes_only_that_scope(store):
    store.put("project/a", "one", "1")
    store.put("project/a", "two", "2")
    store.put("project/b", "one", "1")

    assert store.delete_scope("project/a") == 2
    assert store.list("project/a") == []
    assert store.list("project/b") == ["one"]


def test_a_locked_store_explains_how_to_create_the_key(tmp_path):
    locked = SecretStore(tmp_path / "store.sqlite3", None)

    assert locked.unlocked is False
    with pytest.raises(SecretStoreLocked, match="init-secrets"):
        locked.get("model/planning", "api_key")


def test_materialized_keys_live_in_the_runtime_dir_and_are_removed(store, tmp_path):
    store.put("env/shop-api/dev", "ssh_key", "PRIVATE KEY")
    runtime = tmp_path / "run"

    with materialize(store, "env/shop-api/dev", "ssh_key", runtime) as path:
        assert path.parent == runtime
        assert path.read_text(encoding="utf-8") == "PRIVATE KEY"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.exists()


def test_materialized_keys_are_removed_even_when_the_operation_fails(store, tmp_path):
    store.put("env/shop-api/dev", "ssh_key", "PRIVATE KEY")
    runtime = tmp_path / "run"

    with pytest.raises(RuntimeError, match="boom"):
        with materialize(store, "env/shop-api/dev", "ssh_key", runtime) as path:
            raise RuntimeError("boom")
    assert not path.exists()
    assert list(runtime.iterdir()) == []


def test_write_private_file_refuses_a_non_writable_runtime_dir(tmp_path, monkeypatch):
    runtime = tmp_path / "run"
    runtime.mkdir()

    def broken(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("caliper.secret_files.tempfile.mkstemp", broken)
    with pytest.raises(SecretStoreError, match="Cannot materialize"):
        write_private_file(runtime, "PRIVATE KEY")


def test_secure_unlink_overwrites_then_removes(tmp_path):
    path = tmp_path / "key"
    path.write_text("PRIVATE KEY", encoding="utf-8")

    secure_unlink(path)

    assert not path.exists()


def test_logs_never_contain_a_registered_secret():
    register_secret(VALUE)
    record = logging.LogRecord(
        "caliper.test", logging.ERROR, __file__, 1,
        "request failed with %s", (VALUE,), None,
    )
    output = JsonFormatter().format(record)

    assert VALUE not in output
    assert "[redacted]" in output
    assert VALUE not in redact(f"error: {VALUE}")


def test_json_logs_are_one_object_per_line_with_level_and_logger():
    record = logging.LogRecord("caliper.app", logging.INFO, __file__, 1, "started", (), None)

    line = JsonFormatter().format(record)

    import json
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "caliper.app"
    assert payload["message"] == "started"


def _row(path: Path, scope: str, name: str) -> dict:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    try:
        return dict(connection.execute(
            "SELECT nonce, ciphertext FROM secrets WHERE scope = ? AND name = ?",
            (scope, name),
        ).fetchone())
    finally:
        connection.close()
