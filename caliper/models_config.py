import os
import pathlib
from pathlib import Path

import yaml

from caliper.http_models import ChatClient, HTTPExecutionModel, HTTPPlanningModel, HTTPReviewModel

ROLE_ORDER = ("planning", "execution", "review")
ROLE_LABELS = {"planning": "Planning", "execution": "Execution", "review": "Review"}
DEFAULT_MODELS_PATH = Path(__file__).resolve().parent / "models.yaml"


class ModelsConfigError(Exception):
    pass


class ModelsNotConfigured(ModelsConfigError):
    pass


def _text(value) -> str:
    return str(value or "").strip()


def normalize_settings(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ModelsConfigError("models.yaml must be a mapping of roles to base_url/model.")
    result = {}
    for role in ROLE_ORDER:
        entry = raw.get(role) or {}
        if not isinstance(entry, dict):
            raise ModelsConfigError(f"Role '{role}' must be a mapping.")
        base_url = _text(entry.get("base_url"))
        model = _text(entry.get("model"))
        if bool(base_url) != bool(model):
            raise ModelsConfigError(f"Role '{role}': base URL and model must be set together.")
        result[role] = {"base_url": base_url, "model": model}
    models = [entry["model"] for entry in result.values() if entry["model"]]
    if len(set(models)) != len(models):
        raise ModelsConfigError(
            "Each role must resolve to a DIFFERENT model (got duplicates). "
            "Same model reviewing itself repeats its own blind spots."
        )
    return result


def make_http_adapters(config: dict, keys: dict | None = None, transports: dict | None = None) -> dict:
    """Build HTTP role adapters from a normalized config.

    Runtime never builds mock adapters; tests inject their own fake transports.
    """
    keys = keys or {}
    transports = transports or {}
    classes = {"planning": HTTPPlanningModel, "execution": HTTPExecutionModel, "review": HTTPReviewModel}
    adapters = {}
    for role in ROLE_ORDER:
        entry = config[role]
        if not entry["base_url"] or not entry["model"]:
            raise ModelsNotConfigured(f"Role '{ROLE_LABELS[role]}' is not configured.")
        client = ChatClient(
            entry["model"],
            entry["base_url"],
            api_key=keys.get(role, ""),
            transport=transports.get(role),
        )
        adapters[role] = classes[role](client)
    return adapters


def probe_role(role: str, base_url: str, model: str, api_key: str | None = None, transport=None) -> str:
    base_url = _text(base_url)
    model = _text(model)
    if not base_url or not model:
        raise ModelsConfigError("Base URL and model are required before testing.")
    client = ChatClient(model, base_url, api_key=api_key, timeout=30, transport=transport)
    reply = client.post("Reply with the single word OK.")
    if not reply.strip():
        raise ModelsConfigError("The model returned an empty response.")
    return reply.strip()


class ModelsSettings:
    """Persistent global model settings plus API-key secrets.

    Non-secret fields live in models.yaml (CALIPER_MODELS_CONFIG). API keys live
    under CALIPER_SECRETS_DIR with mode 600 and are never written to YAML.
    """

    def __init__(self, path=None, secrets_dir=None):
        self.path = pathlib.Path(path or os.environ.get("CALIPER_MODELS_CONFIG", DEFAULT_MODELS_PATH))
        default_secrets = Path(__file__).resolve().parent.parent / ".caliper-state" / "secrets"
        self.secrets_dir = pathlib.Path(secrets_dir or os.environ.get("CALIPER_SECRETS_DIR", default_secrets))

    def key_path(self, role: str) -> Path:
        return self.secrets_dir / f"{role}.key"

    def api_key(self, role: str) -> str:
        path = self.key_path(role)
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def api_keys(self) -> dict:
        return {role: self.api_key(role) for role in ROLE_ORDER}

    def load(self) -> dict:
        if not self.path.is_file():
            return normalize_settings({})
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ModelsConfigError(f"models.yaml is not valid YAML: {exc}")
        return normalize_settings(raw)

    def configured(self) -> bool:
        config = self.load()
        return all(config[role]["base_url"] and config[role]["model"] for role in ROLE_ORDER)

    def missing_roles(self) -> list[str]:
        config = self.load()
        return [ROLE_LABELS[role] for role in ROLE_ORDER if not (config[role]["base_url"] and config[role]["model"])]

    def status(self, values: dict | None = None) -> dict:
        config = self.load()
        result = {}
        for role in ROLE_ORDER:
            entry = (values or {}).get(role) or config[role]
            base_url = _text(entry.get("base_url"))
            model = _text(entry.get("model"))
            result[role] = {
                "label": ROLE_LABELS[role],
                "base_url": base_url,
                "model": model,
                "has_api_key": self.key_path(role).is_file(),
                "configured": bool(base_url and model),
            }
        return result

    def save(self, entries: dict, keys: dict | None = None, clear_keys: dict | None = None) -> dict:
        config = normalize_settings(entries)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# Caliper model settings. Edit through Settings > Models.\n"
            "# API keys are NOT stored here; they live under CALIPER_SECRETS_DIR.\n"
        )
        payload = {role: {"base_url": config[role]["base_url"], "model": config[role]["model"]} for role in ROLE_ORDER}
        self.path.write_text(header + yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        for role in ROLE_ORDER:
            if (clear_keys or {}).get(role):
                self.key_path(role).unlink(missing_ok=True)
                continue
            key = _text((keys or {}).get(role))
            if key:
                self._write_secret(role, key)
        return config

    def _write_secret(self, role: str, key: str) -> None:
        path = self.key_path(role)
        path.write_text(key + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def make_adapters(self, transports: dict | None = None) -> dict:
        config = self.load()
        missing = [ROLE_LABELS[role] for role in ROLE_ORDER if not (config[role]["base_url"] and config[role]["model"])]
        if missing:
            raise ModelsNotConfigured("Models are not configured: " + ", ".join(missing) + ". Open Settings > Models.")
        return make_http_adapters(config, self.api_keys(), transports=transports)
