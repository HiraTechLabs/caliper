import os
import pathlib
from pathlib import Path

import yaml

from caliper.http_models import ChatClient, HTTPExecutionModel, HTTPPlanningModel, HTTPReviewModel
from caliper.opencode_models import DEFAULT_TIMEOUT as OPENCODE_DEFAULT_TIMEOUT
from caliper.opencode_models import OpenCodeClient

ROLE_ORDER = ("planning", "execution", "review")
ROLE_LABELS = {"planning": "Planning", "execution": "Execution", "review": "Review"}
PROVIDER_ORDER = ("http", "opencode")
PROVIDER_LABELS = {"http": "OpenAI-compatible HTTP", "opencode": "OpenCode CLI"}
DEFAULT_MODELS_PATH = Path(__file__).resolve().parent / "models.yaml"
DEFAULT_TIMEOUT = 600


class ModelsConfigError(Exception):
    pass


class ModelsNotConfigured(ModelsConfigError):
    pass


def _text(value) -> str:
    return str(value or "").strip()


def _role_entry(role: str, raw: dict) -> dict:
    """Validate and normalize one role entry, including its provider."""
    if not isinstance(raw, dict):
        raise ModelsConfigError(f"Role '{role}' must be a mapping.")
    provider = _text(raw.get("provider")) or "http"
    if provider not in PROVIDER_ORDER:
        raise ModelsConfigError(f"Role '{role}': provider must be one of {', '.join(PROVIDER_ORDER)}.")
    base_url = _text(raw.get("base_url"))
    model = _text(raw.get("model"))
    executable = _text(raw.get("executable")) or "opencode"
    timeout = _timeout(role, raw.get("timeout"))
    if provider == "opencode":
        if base_url:
            raise ModelsConfigError(
                f"Role '{role}': an OpenCode role is run through the CLI and does not use a base URL."
            )
        if model and "/" not in model:
            raise ModelsConfigError(
                f"Role '{role}': an OpenCode model id must be in 'provider/model' form "
                "(run `opencode models` to list the available ones)."
            )
    elif bool(base_url) != bool(model):
        raise ModelsConfigError(f"Role '{role}': base URL and model must be set together.")
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "executable": executable,
        "timeout": timeout,
    }


def _timeout(role: str, value) -> float:
    if value in (None, ""):
        return DEFAULT_TIMEOUT
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise ModelsConfigError(f"Role '{role}': timeout must be a number of seconds.")
    if timeout <= 0:
        raise ModelsConfigError(f"Role '{role}': timeout must be a positive number of seconds.")
    return timeout


def role_ready(role: str, entry: dict) -> bool:
    """Whether one role carries everything its provider needs to run."""
    entry = entry or {}
    model = _text(entry.get("model"))
    if not model:
        return False
    return _text(entry.get("provider")) == "opencode" or bool(_text(entry.get("base_url")))


def normalize_settings(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ModelsConfigError("models.yaml must be a mapping of roles to provider/base_url/model.")
    result = {}
    for role in ROLE_ORDER:
        result[role] = _role_entry(role, raw.get(role) or {})
    models = [entry["model"] for entry in result.values() if entry["model"]]
    if len(set(models)) != len(models):
        raise ModelsConfigError(
            "Each role must resolve to a DIFFERENT model (got duplicates). "
            "The model id is the identity of the model; its provider is only a transport, "
            "so the same model id may not serve two roles even across providers. "
            "Same model reviewing itself repeats its own blind spots."
        )
    return result


def make_http_adapters(config: dict, keys: dict | None = None, transports: dict | None = None) -> dict:
    """Build role adapters from a normalized config.

    Runtime never builds mock adapters; tests inject their own fake transports
    (``transport`` for HTTP roles, ``runner`` for OpenCode roles). A role's
    provider decides which client backs its adapter; the three role classes only
    need a ``post`` method.
    """
    keys = keys or {}
    transports = transports or {}
    classes = {"planning": HTTPPlanningModel, "execution": HTTPExecutionModel, "review": HTTPReviewModel}
    adapters = {}
    for role in ROLE_ORDER:
        entry = _role_entry(role, config[role])
        if not role_ready(role, entry):
            raise ModelsNotConfigured(f"Role '{ROLE_LABELS[role]}' is not configured.")
        if entry["provider"] == "opencode":
            client: object = OpenCodeClient(
                entry["model"],
                executable=entry["executable"],
                timeout=entry["timeout"] or OPENCODE_DEFAULT_TIMEOUT,
            )
        else:
            client = ChatClient(
                entry["model"],
                entry["base_url"],
                api_key=keys.get(role, ""),
                timeout=entry["timeout"] or DEFAULT_TIMEOUT,
                transport=transports.get(role),
            )
        adapters[role] = classes[role](client)
    return adapters


def probe_role(role: str, base_url: str, model: str, api_key: str | None = None, transport=None,
               provider: str = "http", timeout: float = 60, runner=None) -> str:
    base_url = _text(base_url)
    model = _text(model)
    provider = _text(provider) or "http"
    if provider == "opencode":
        if not model:
            raise ModelsConfigError("Model id is required before testing.")
        client = OpenCodeClient(model, timeout=timeout, runner=runner)
    else:
        if not base_url or not model:
            raise ModelsConfigError("Base URL and model are required before testing.")
        client = ChatClient(model, base_url, api_key=api_key, timeout=timeout, transport=transport)
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
        return all(role_ready(role, config[role]) for role in ROLE_ORDER)

    def missing_roles(self) -> list[str]:
        config = self.load()
        return [ROLE_LABELS[role] for role in ROLE_ORDER if not role_ready(role, config[role])]

    def status(self, values: dict | None = None) -> dict:
        config = self.load()
        result = {}
        for role in ROLE_ORDER:
            entry = dict(config[role])
            entry.update((values or {}).get(role) or {})
            provider = _text(entry.get("provider")) or "http"
            base_url = _text(entry.get("base_url"))
            model = _text(entry.get("model"))
            result[role] = {
                "label": ROLE_LABELS[role],
                "provider": provider,
                "provider_label": PROVIDER_LABELS.get(provider, provider),
                "base_url": base_url,
                "model": model,
                "has_api_key": self.key_path(role).is_file(),
                "configured": bool(model) and (provider == "opencode" or bool(base_url)),
            }
        return result

    def save(self, entries: dict, keys: dict | None = None, clear_keys: dict | None = None) -> dict:
        config = normalize_settings(entries)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# Caliper model settings (source of truth for the three roles).\n"
            "# Edit through Settings > Models. API keys are NOT stored here; they live\n"
            "# under CALIPER_SECRETS_DIR with mode 600.\n"
            "# The three roles MUST resolve to three different models.\n"
        )
        payload = {
            role: {
                "provider": config[role]["provider"],
                "base_url": config[role]["base_url"],
                "model": config[role]["model"],
                "timeout": config[role]["timeout"],
                **({"executable": config[role]["executable"]}
                   if config[role]["provider"] == "opencode" else {}),
            }
            for role in ROLE_ORDER
        }
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
        """Create/overwrite the key file with mode 0600 in one step (no chmod window)."""
        path = self.key_path(role)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, (key + "\n").encode("utf-8"))
        finally:
            os.close(fd)

    def make_adapters(self, transports: dict | None = None) -> dict:
        config = self.load()
        missing = [ROLE_LABELS[role] for role in ROLE_ORDER if not role_ready(role, config[role])]
        if missing:
            raise ModelsNotConfigured("Models are not configured: " + ", ".join(missing) + ". Open Settings > Models.")
        return make_http_adapters(config, self.api_keys(), transports=transports)
