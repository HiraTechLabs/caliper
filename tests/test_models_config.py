import pytest
import yaml

from caliper.models_config import ROLE_ORDER, ModelsConfigError, load_models_config, validate_models_config


def _config(**adapters) -> dict:
    result = {}
    for role in ROLE_ORDER:
        adapter = adapters.get(role, "mock")
        entry = {"model": f"{role}-model", "adapter": adapter}
        if adapter == "http":
            entry["base_url"] = "http://127.0.0.1:9999/v1"
        result[role] = entry
    return result


def test_valid_config_passes(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(_config()))
    config = load_models_config(path)
    assert config["planning"]["model"] != config["review"]["model"]


def test_missing_file_fails(tmp_path):
    with pytest.raises(ModelsConfigError, match="not found"):
        load_models_config(tmp_path / "absent.yaml")


def test_duplicate_roles_config_fails(tmp_path):
    broken = _config()
    broken["review"]["model"] = broken["planning"]["model"]
    with pytest.raises(ModelsConfigError, match="DIFFERENT"):
        validate_models_config(broken)


def test_unknown_adapter_fails():
    config = _config()
    config["execution"]["adapter"] = "openai"
    with pytest.raises(ModelsConfigError, match="adapter"):
        validate_models_config(config)


def test_http_adapter_requires_base_url():
    config = _config(execution="http")
    config["execution"].pop("base_url")
    with pytest.raises(ModelsConfigError, match="base_url"):
        validate_models_config(config)


def test_missing_role_fails():
    config = _config()
    config.pop("review")
    with pytest.raises(ModelsConfigError, match="roles"):
        validate_models_config(config)
