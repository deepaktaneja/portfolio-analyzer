"""Encrypted backup CLI.

Usage:
  python -m scripts.backup --dest-dir data/backups
  BACKUP_ENCRYPTION_KEY=... python -m scripts.backup
"""
from __future__ import annotations

import argparse
import json
import os


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.backup")
    parser.add_argument("--dest-dir", default="data/backups")
    parser.add_argument("--key", default=os.environ.get("BACKUP_ENCRYPTION_KEY"))
    parser.add_argument("--db", nargs="*", help="explicit db paths (defaults to all known DBs)")
    args = parser.parse_args(argv)

    if not args.key:
        print("error: no encryption key (set BACKUP_ENCRYPTION_KEY or pass --key)")
        return 2
    from security.backup import backup_all

    results = backup_all(db_paths=args.db or None, dest_dir=args.dest_dir, key=args.key)
    print(json.dumps({"backups": results}, indent=2, default=str))
    return 0 if results else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
