"""Runtime configuration and master-key handling (PLAN_P1 phase 1.1/1.2)."""

import base64
import os
import stat

import pytest

from caliper.config import (
    CaliperConfig,
    ConfigError,
    decode_master_key,
    init_master_key,
    load_master_key,
    read_secret_value,
)

KEY = bytes(range(33, 65))


def test_all_paths_live_under_the_data_dir(tmp_path):
    config = CaliperConfig.from_env({"CALIPER_DATA_DIR": str(tmp_path)})

    assert config.data_dir == tmp_path
    assert config.db_path == tmp_path / "caliper.db"
    assert config.projects_root == tmp_path / "projects"
    assert config.work_root == tmp_path / "work"
    assert config.secrets_dir == tmp_path / "secrets"
    assert config.models_config == tmp_path / "models.yaml"
    assert config.secret_store_path == tmp_path / "secrets" / "store.sqlite3"


def test_every_path_can_be_overridden(tmp_path):
    config = CaliperConfig.from_env({
        "CALIPER_DATA_DIR": str(tmp_path / "data"),
        "CALIPER_DB_PATH": str(tmp_path / "db" / "run.sqlite3"),
        "CALIPER_PROJECTS_ROOT": str(tmp_path / "p"),
        "CALIPER_WORK_ROOT": str(tmp_path / "w"),
        "CALIPER_SECRETS_DIR": str(tmp_path / "s"),
        "CALIPER_MODELS_CONFIG": str(tmp_path / "m.yaml"),
        "CALIPER_RUNTIME_DIR": str(tmp_path / "run"),
        "CALIPER_HOST": "0.0.0.0",
        "CALIPER_PORT": "9000",
    })

    assert config.db_path == tmp_path / "db" / "run.sqlite3"
    assert config.projects_root == tmp_path / "p"
    assert config.work_root == tmp_path / "w"
    assert config.secrets_dir == tmp_path / "s"
    assert config.models_config == tmp_path / "m.yaml"
    assert config.runtime_dir == tmp_path / "run"
    assert (config.host, config.port) == ("0.0.0.0", 9000)


def test_secret_like_values_support_a_file_variant(tmp_path):
    path = tmp_path / "key"
    path.write_text("from-file\n", encoding="utf-8")

    assert read_secret_value("CALIPER_MASTER_KEY", {
        "CALIPER_MASTER_KEY": "from-env",
        "CALIPER_MASTER_KEY_FILE": str(path),
    }) == "from-file"


def test_an_unreadable_secret_file_is_an_error_not_a_fallback(tmp_path):
    with pytest.raises(ConfigError, match="cannot be read"):
        read_secret_value("CALIPER_MASTER_KEY", {
            "CALIPER_MASTER_KEY": "from-env",
            "CALIPER_MASTER_KEY_FILE": str(tmp_path / "missing"),
        })


@pytest.mark.parametrize("value", [
    base64.urlsafe_b64encode(KEY).decode("ascii"),
    KEY.hex(),
    KEY.decode("latin-1"),
])
def test_the_master_key_accepts_base64_hex_and_raw_forms(value):
    assert decode_master_key(value) == KEY


def test_a_missing_or_invalid_master_key_refuses_startup_with_the_command():
    with pytest.raises(ConfigError, match="init-secrets"):
        load_master_key({})
    with pytest.raises(ConfigError, match="32 bytes"):
        load_master_key({"CALIPER_MASTER_KEY": "too short"})


def test_init_secrets_writes_a_owner_only_key(tmp_path):
    path = init_master_key(tmp_path / "secrets" / "master.key")

    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(decode_master_key(path.read_text(encoding="utf-8"))) == 32
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_init_secrets_refuses_to_overwrite_without_force(tmp_path):
    path = init_master_key(tmp_path / "master.key")
    original = path.read_text(encoding="utf-8")

    with pytest.raises(ConfigError, match="already exists"):
        init_master_key(path)
    assert path.read_text(encoding="utf-8") == original

    init_master_key(path, force=True)
    assert path.read_text(encoding="utf-8") != original


def test_init_secrets_deletes_its_temporary_file_on_replace_failure(tmp_path, monkeypatch):
    target = tmp_path / "master.key"

    def broken_replace(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("caliper.config.os.replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        init_master_key(target)
    assert not list(tmp_path.glob(".master-key-*"))
    assert not target.exists()


def test_directories_are_created_with_the_expected_modes(tmp_path):
    config = CaliperConfig.from_env({
        "CALIPER_DATA_DIR": str(tmp_path / "data"),
        "CALIPER_RUNTIME_DIR": str(tmp_path / "run"),
    })
    config.ensure_directories()

    assert config.data_dir.is_dir()
    assert config.projects_root.is_dir()
    assert config.work_root.is_dir()
    assert config.secrets_dir.is_dir()
    assert config.runtime_dir.is_dir()
    if os.name != "nt":
        assert stat.S_IMODE(config.secrets_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(config.runtime_dir.stat().st_mode) == 0o700
