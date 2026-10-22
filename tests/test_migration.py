"""One-way migration from the Phase 0c plaintext layout (PLAN_P1 1.2)."""

from pathlib import Path

import pytest

from caliper.config import CaliperConfig
from caliper.migrate import MigrationError, migrate_legacy, migrate_plaintext_secrets
from caliper.secrets import SecretStore
from caliper.state import RequestStore

KEY = bytes(range(32, 64))
OTHER_KEY = bytes(reversed(range(32, 64)))
VALUE = "sk-legacy-model-key"


def _config(tmp_path) -> CaliperConfig:
    data = tmp_path / "data"
    return CaliperConfig(
        data_dir=data,
        db_path=data / "caliper.db",
        projects_root=data / "projects",
        work_root=data / "work",
        secrets_dir=data / "secrets",
        models_config=data / "models.yaml",
        runtime_dir=tmp_path / "run",
    )


def _write(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def _legacy_secrets(config: CaliperConfig) -> Path:
    return config.legacy_secrets_dir()


def test_legacy_database_is_copied_and_the_only_copy_is_kept(tmp_path):
    config = _config(tmp_path)
    legacy = RequestStore(config.legacy_db_path())
    legacy.create("shop-api", "feature", "Add refunds")
    store = SecretStore(config.secret_store_path, KEY)

    report = migrate_legacy(config, store)

    assert report["database"] is True
    assert config.legacy_db_path().is_file()
    copied = RequestStore(config.db_path)
    assert copied.list_all()[0]["text"] == "Add refunds"


def test_all_plaintext_keys_are_encrypted_and_removed(tmp_path):
    config = _config(tmp_path)
    root = _legacy_secrets(config)
    planning = _write(root / "planning.key", VALUE)
    deploy = _write(root / "projects" / "shop-api" / "repo_deploy", "PRIVATE DEPLOY")
    deploy_pub = _write(root / "projects" / "shop-api" / "repo_deploy.pub", "ssh-ed25519 DEPLOY")
    env = _write(root / "projects" / "shop-api" / "env_dev", "PRIVATE ENV")
    env_pub = _write(root / "projects" / "shop-api" / "env_dev.pub", "ssh-ed25519 ENV")
    known_hosts = _write(
        root / "projects" / "shop-api" / "known_hosts_dev", "dev.example.com ssh-ed25519 AAAA"
    )
    store = SecretStore(config.secret_store_path, KEY)

    migrate_legacy(config, store)

    assert store.get("model/planning", "api_key") == VALUE
    assert store.get("project/shop-api", "deploy_key") == "PRIVATE DEPLOY"
    assert store.get("project/shop-api", "deploy_key.pub") == "ssh-ed25519 DEPLOY"
    assert store.get("env/shop-api/dev", "ssh_key") == "PRIVATE ENV"
    assert store.get("env/shop-api/dev", "ssh_key.pub") == "ssh-ed25519 ENV"
    for path in (planning, deploy, deploy_pub, env, env_pub):
        assert not path.exists()
    assert not known_hosts.exists()
    migrated_known_hosts = config.secrets_dir / "projects" / "shop-api" / "known_hosts_dev"
    assert migrated_known_hosts.is_file()


def test_running_the_migration_again_is_safe(tmp_path):
    config = _config(tmp_path)
    _write(_legacy_secrets(config) / "planning.key", VALUE)
    store = SecretStore(config.secret_store_path, KEY)

    migrate_legacy(config, store)
    migrate_legacy(config, store)

    assert store.get("model/planning", "api_key") == VALUE


def test_a_different_existing_value_is_never_overwritten(tmp_path):
    config = _config(tmp_path)
    plaintext = _write(_legacy_secrets(config) / "projects" / "shop-api" / "repo_deploy", "OLD")
    store = SecretStore(config.secret_store_path, KEY)
    store.put("project/shop-api", "deploy_key", "NEW")

    with pytest.raises(MigrationError, match="different value"):
        migrate_plaintext_secrets(config, store)

    assert store.get("project/shop-api", "deploy_key") == "NEW"
    # The only copy of the old value was not deleted.
    assert plaintext.read_text(encoding="utf-8") == "OLD"


def test_a_wrong_master_key_does_not_destroy_the_plaintext_copy(tmp_path):
    config = _config(tmp_path)
    path = config.secret_store_path
    good = SecretStore(path, KEY)
    good.put("model/planning", "api_key", VALUE)
    good.close()
    plaintext = _write(_legacy_secrets(config) / "planning.key", "OLD PLAINTEXT")
    wrong = SecretStore(path, OTHER_KEY)

    with pytest.raises(MigrationError, match="does not match"):
        migrate_plaintext_secrets(config, wrong)

    assert plaintext.read_text(encoding="utf-8") == "OLD PLAINTEXT"
    restored = SecretStore(path, KEY)
    assert restored.get("model/planning", "api_key") == VALUE


def test_a_new_install_has_nothing_to_migrate(tmp_path):
    config = _config(tmp_path)
    store = SecretStore(config.secret_store_path, KEY)

    report = migrate_legacy(config, store)

    assert report["database"] is False
    assert not config.db_path.exists()
