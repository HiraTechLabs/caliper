"""Smoke-test one configured model role against its real provider.

Usage (inside the Caliper environment):

    python -m caliper.smoke planning|execution|review [--timeout N]

Loads the role from models.yaml (CALIPER_MODELS_CONFIG), reads its saved API
key from CALIPER_SECRETS_DIR when the provider is HTTP, sends one tiny
prompt and prints the reply. Secrets are never printed; exit code 1 on
failure. This exercises the real endpoint, so use it as a smoke test only.
"""

import argparse
import sys

from caliper.models_config import ModelsSettings, ROLE_LABELS, ROLE_ORDER, probe_role


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m caliper.smoke",
        description="Smoke-test one configured model role against its real provider.",
    )
    parser.add_argument("role", choices=ROLE_ORDER)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)

    settings = ModelsSettings()
    entry = settings.load()[args.role]
    if entry["provider"] == "opencode":
        print(f"{ROLE_LABELS[args.role]}: OpenCode CLI, model {entry['model']}")
    else:
        print(f"{ROLE_LABELS[args.role]}: {entry['base_url']}, model {entry['model']}")
    if not entry["model"]:
        print("FAIL: the role has no model configured. Open Settings > Models.", file=sys.stderr)
        return 1
    try:
        reply = probe_role(
            args.role,
            entry["base_url"],
            entry["model"],
            settings.api_key(args.role),
            provider=entry["provider"],
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {reply[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
