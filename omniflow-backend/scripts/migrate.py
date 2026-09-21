#!/usr/bin/env python3
"""
scripts/migrate.py — Run Alembic migrations programmatically.

Usage:
    # From omniflow-backend/ with Docker Compose running:
    .\.venv\Scripts\python.exe scripts/migrate.py upgrade head
    .\.venv\Scripts\python.exe scripts/migrate.py downgrade -1
    .\.venv\Scripts\python.exe scripts/migrate.py current
    .\.venv\Scripts\python.exe scripts/migrate.py history

Equivalent to running `alembic <command>` but works cross-platform
without needing alembic on PATH.
"""
from __future__ import annotations

import sys
from alembic.config import Config
from alembic import command


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: migrate.py <alembic-command> [options]")
        print("  upgrade head | downgrade -1 | current | history | heads")
        sys.exit(1)

    cfg = Config("alembic.ini")
    cmd_name = args[0]
    cmd_args = args[1:]

    dispatch = {
        "upgrade": lambda: command.upgrade(cfg, cmd_args[0] if cmd_args else "head"),
        "downgrade": lambda: command.downgrade(cfg, cmd_args[0] if cmd_args else "-1"),
        "current": lambda: command.current(cfg, verbose=True),
        "history": lambda: command.history(cfg, verbose=True),
        "heads": lambda: command.heads(cfg, verbose=True),
        "stamp": lambda: command.stamp(cfg, cmd_args[0] if cmd_args else "head"),
    }

    if cmd_name not in dispatch:
        print(f"Unknown command: {cmd_name}")
        print(f"Available: {', '.join(dispatch)}")
        sys.exit(1)

    try:
        dispatch[cmd_name]()
    except Exception as e:
        print(f"\n[ERROR] Migration failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
