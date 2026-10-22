import os
import stat

import pytest
import yaml

from caliper.models_config import (
    ROLE_ORDER,
    ModelsConfigError,
    ModelsNotConfigured,
    ModelsSettings,
    probe_role,
)


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


def test_provider_defaults_to_http_and_is_persisted(tmp_path):
    settings = _settings(tmp_path)
    config = settings.save(_entries())
    assert all(config[role]["provider"] == "http" for role in ROLE_ORDER)
    stored = yaml.safe_load(settings.path.read_text(encoding="utf-8"))
    assert stored["planning"]["provider"] == "http"
    assert settings.configured() is True


def test_unknown_provider_is_rejected(tmp_path):
    settings = _settings(tmp_path)
    entries = _entries()
    entries["planning"]["provider"] = "warp"
    with pytest.raises(ModelsConfigError, match="provider must be one of http, opencode"):
        settings.save(entries)


def test_opencode_role_needs_a_provider_slash_model_id(tmp_path):
    settings = _settings(tmp_path)
    entries = _entries()
    entries["planning"].update({"provider": "opencode", "base_url": ""})
    entries["planning"]["model"] = "glm-free-no-slash"
    with pytest.raises(ModelsConfigError, match="provider/model"):
        settings.save(entries)


def test_opencode_role_rejects_a_base_url(tmp_path):
    settings = _settings(tmp_path)
    entries = _entries()
    entries["planning"].update({"provider": "opencode", "model": "opencode/glm-5.3-flash-free"})
    with pytest.raises(ModelsConfigError, match="does not use a base URL"):
        settings.save(entries)


def test_opencode_role_needs_no_base_url_to_be_configured(tmp_path):
    settings = _settings(tmp_path)
    entries = _entries()
    entries["planning"].update({"provider": "opencode", "base_url": ""})
    entries["planning"]["model"] = "opencode/glm-5.3-flash-free"
    config = settings.save(entries)
    assert settings.configured() is True
    assert settings.missing_roles() == []
    assert config["planning"]["base_url"] == ""


def test_opencode_role_builds_an_opencode_backed_adapter(tmp_path):
    settings = _settings(tmp_path)
    entries = _entries()
    entries["execution"].update({"provider": "opencode", "base_url": ""})
    entries["execution"]["model"] = "opencode/glm-5.3-flash-free"
    settings.save(entries)
    adapters = settings.make_adapters()
    assert type(adapters["execution"].client).__name__ == "OpenCodeClient"
    assert adapters["execution"].client.model == "opencode/glm-5.3-flash-free"


def test_executable_round_trips_in_models_yaml(tmp_path):
    settings = _settings(tmp_path)
    entries = _entries()
    entries["execution"].update({
        "provider": "opencode", "base_url": "", "model": "opencode/glm-5.3-flash-free",
        "executable": "/opt/opencode/bin/opencode",
    })

    settings.save(entries)

    stored = yaml.safe_load(settings.path.read_text(encoding="utf-8"))
    assert stored["execution"]["executable"] == "/opt/opencode/bin/opencode"
    assert settings.load()["execution"]["executable"] == "/opt/opencode/bin/opencode"
    adapters = settings.make_adapters()
    assert adapters["execution"].client.executable == "/opt/opencode/bin/opencode"


def test_secret_files_are_written_without_a_chmod_window(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    calls = []

    def forbidden_chmod(*args, **kwargs):
        calls.append(args)
        raise AssertionError("secrets must be created with os.open(mode=0o600), not chmod-ed")

    monkeypatch.setattr("caliper.models_config.os.chmod", forbidden_chmod)

    settings.save(_entries(), keys={"planning": "sk-planning"})

    assert calls == []
    assert stat.S_IMODE(settings.key_path("planning").stat().st_mode) == 0o600
    assert settings.api_key("planning") == "sk-planning"


def test_an_existing_world_readable_secret_is_tightened_on_save(tmp_path):
    settings = _settings(tmp_path)
    settings.secrets_dir.mkdir(parents=True, exist_ok=True)
    path = settings.key_path("planning")
    path.write_text("old-key\n", encoding="utf-8")
    os.chmod(path, 0o644)

    settings.save(_entries(), keys={"planning": "sk-new"})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert settings.api_key("planning") == "sk-new"


def test_timeout_is_validated_and_stored(tmp_path):
    settings = _settings(tmp_path)
    entries = _entries()
    entries["review"]["timeout"] = 120
    config = settings.save(entries)
    assert config["review"]["timeout"] == 120
    entries["review"]["timeout"] = -5
    with pytest.raises(ModelsConfigError, match="positive"):
        settings.save(entries)
    entries["review"]["timeout"] = "later"
    with pytest.raises(ModelsConfigError, match="number of seconds"):
        settings.save(entries)


def test_the_model_id_is_the_identity_across_providers(tmp_path):
    settings = _settings(tmp_path)
    entries = _entries(planning="opencode/same-model", execution="opencode/same-model")
    for role in ("planning", "execution"):
        entries[role].update({"provider": "opencode", "base_url": ""})
    with pytest.raises(ModelsConfigError, match="DIFFERENT"):
        settings.save(entries)


def test_probe_role_sends_the_ok_prompt_over_opencode():
    stored = []

    def runner(client, content):
        stored.append((client.model, content))
        return '{"type":"text","part":{"type":"text","text":"OK"}}'

    reply = probe_role("review", "", "opencode/glm-5.3-flash-free", provider="opencode", runner=runner)
    assert reply == "OK"
    assert stored[0][0] == "opencode/glm-5.3-flash-free"


def test_probe_role_rejects_opencode_without_a_model():
    with pytest.raises(ModelsConfigError, match="required before testing"):
        probe_role("review", "", "", provider="opencode")


def test_probe_role_http_path_is_unchanged():
    with pytest.raises(ModelsConfigError, match="required before testing"):
        probe_role("review", "", "", provider="http")
    with pytest.raises(ModelsConfigError, match="required before testing"):
        probe_role("review", "http://x/v1", "", provider="http")
