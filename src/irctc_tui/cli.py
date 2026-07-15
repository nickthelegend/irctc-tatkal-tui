"""Command-line entry point: ``irctc-tui`` / ``python -m irctc_tui``."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .config import default_config_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="irctc-tui",
        description="Terminal UI to automate IRCTC Tatkal form-filling and "
        "availability polling. You solve the CAPTCHA, login, and payment.",
        epilog="Config is read from and saved to ./config.json unless --config is given.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help=f"Path to the JSON config file (default: {default_config_path()}).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Import here so ``--help`` / ``--version`` don't pay the Textual import cost.
    from .app import run

    run(config_path=args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
