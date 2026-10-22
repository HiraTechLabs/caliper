"""Caliper command line: serve the app or create the master key.

``python -m caliper`` starts the server (the Phase 0 behaviour).
``python -m caliper init-secrets`` writes a new 32-byte master key with mode
0600. It deliberately imports nothing from :mod:`caliper.main`, so it works
before the secret store can be unlocked.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from caliper.config import CaliperConfig, ConfigError, init_master_key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="caliper", description="Private software factory.")
    subcommands = parser.add_subparsers(dest="command")
    init = subcommands.add_parser(
        "init-secrets",
        help="Create the master key that encrypts the secret store.",
    )
    init.add_argument(
        "--output",
        default=None,
        help="Where to write the key (default: CALIPER_SECRETS_DIR/master.key).",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing key only after confirming no data needs it.",
    )
    serve = subcommands.add_parser("serve", help="Start the Caliper server (default).")
    serve.add_argument("--host", default=None, help="Bind address (default: CALIPER_HOST or 127.0.0.1).")
    serve.add_argument("--port", type=int, default=None, help="Port (default: CALIPER_PORT or 8080).")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init-secrets":
        config = CaliperConfig.from_env()
        path = Path(args.output) if args.output else config.master_key_path
        try:
            written = init_master_key(path, force=args.force)
        except ConfigError as exc:
            print(f"Caliper: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote a new master key to {written} (mode 0600).")
        print(
            "Start Caliper with CALIPER_MASTER_KEY_FILE pointing at this file. "
            "Keep a backup: losing it makes every stored secret unreadable."
        )
        return 0
    if args.command in (None, "serve"):
        if args.command == "serve":
            if args.host:
                import os

                os.environ["CALIPER_HOST"] = args.host
            if args.port:
                import os

                os.environ["CALIPER_PORT"] = str(args.port)
        from caliper.main import run

        run()
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
