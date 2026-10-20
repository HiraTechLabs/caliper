import pytest
import yaml

from caliper.models_config import ModelsConfigError, ModelsNotConfigured, ModelsSettings, ROLE_ORDER


def _settings(tmp_path):
    return ModelsSettings(tmp_path / "models.yaml", tmp_path / "secrets")


def _entries(**models):
    return {
        role: {
            "base_url": f"http://127.0.0.1:{9000 + index}/v1",
            "model": models.get(role, f"{role}-model"),
        }
        for index, role in enumerate(ROLE_ORDER)
    }


def test_save_persists_models_and_keys_outside_yaml(tmp_path):
    settings = _settings(tmp_path)
    settings.save(_entries(), keys={"planning": "sk-planning"})

    stored = yaml.safe_load(settings.path.read_text(encoding="utf-8"))
    assert stored["planning"]["model"] == "planning-model"
    assert "sk-planning" not in settings.path.read_text(encoding="utf-8")
    assert settings.api_key("planning") == "sk-planning"
    assert settings.configured() is True


def test_duplicate_roles_are_rejected(tmp_path):
    settings = _settings(tmp_path)
    with pytest.raises(ModelsConfigError, match="DIFFERENT"):
        settings.save(_entries(planning="same", execution="same"))


def test_partial_role_is_rejected(tmp_path):
    settings = _settings(tmp_path)
    entries = _entries()
    entries["review"]["base_url"] = ""
    with pytest.raises(ModelsConfigError, match="together"):
        settings.save(entries)


def test_missing_file_is_unconfigured(tmp_path):
    settings = _settings(tmp_path)
    assert settings.configured() is False
    assert settings.missing_roles() == ["Planning", "Execution", "Review"]
    with pytest.raises(ModelsNotConfigured, match="not configured"):
        settings.make_adapters()


def test_saved_config_builds_three_distinct_http_adapters(tmp_path):
    settings = _settings(tmp_path)
    settings.save(_entries())
    adapters = settings.make_adapters()
    assert set(adapters) == set(ROLE_ORDER)
    assert len({adapter.__class__ for adapter in adapters.values()}) == 3
