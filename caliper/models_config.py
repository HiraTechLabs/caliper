import pathlib

import yaml

ROLE_ORDER = ("planning", "execution", "review")


class ModelsConfigError(Exception):
    pass


def load_models_config(path) -> dict:
    path = pathlib.Path(path)
    if not path.is_file():
        raise ModelsConfigError(f"Models config '{path}' not found. Caliper cannot start without it.")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_models_config(config)
    return config


def validate_models_config(config: dict) -> None:
    if not isinstance(config, dict) or set(config) != set(ROLE_ORDER):
        raise ModelsConfigError("models.yaml must define exactly the roles: planning, execution, review.")
    models = []
    for role in ROLE_ORDER:
        entry = config[role]
        if not isinstance(entry, dict) or "model" not in entry or "adapter" not in entry:
            raise ModelsConfigError(f"Role '{role}' must define 'model' and 'adapter'.")
        if entry["adapter"] not in ("mock", "http"):
            raise ModelsConfigError(f"Role '{role}': adapter must be 'mock' or 'http'.")
        if entry["adapter"] == "http" and not entry.get("base_url"):
            raise ModelsConfigError(f"Role '{role}': adapter 'http' requires a 'base_url'.")
        models.append(entry["model"])
    if len(set(models)) != len(models):
        raise ModelsConfigError("Each role must resolve to a DIFFERENT model (got duplicates). Same model reviewing itself repeats its own blind spots.")
