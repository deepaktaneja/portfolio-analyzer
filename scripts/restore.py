"""Encrypted backup restore CLI (verifies before overwriting).

Usage:
  python -m scripts.restore data/backups/xirr.sqlite3.<ts>.db.enc data/xirr.sqlite3
"""
from __future__ import annotations

import argparse
import json
import os


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.restore")
    parser.add_argument("backup")
    parser.add_argument("target")
    parser.add_argument("--key", default=os.environ.get("BACKUP_ENCRYPTION_KEY"))
    parser.add_argument("--no-verify", action="store_true", help="skip integrity verification (not recommended)")
    args = parser.parse_args(argv)

    if not args.key:
        print("error: no encryption key (set BACKUP_ENCRYPTION_KEY or pass --key)")
        return 2
    from security.backup import restore_database

    result = restore_database(args.backup, args.target, args.key, verify=not args.no_verify)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("restored") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
