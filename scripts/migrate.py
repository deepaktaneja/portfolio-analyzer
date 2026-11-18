"""Database migration + provisioning.

Runs Alembic ``upgrade`` for the alembic-managed core DB, then provisions the
self-managing (independent-base) databases so a fresh deploy has all schemas.

Usage:
  python -m scripts.migrate            # upgrade head + provision
  python -m scripts.migrate revision   # upgrade to a specific revision
"""
from __future__ import annotations

import argparse
from pathlib import Path


def _provision_standalone() -> list[str]:
    """Create schemas for self-provisioning databases (idempotent)."""
    created = []
    for label, fn in (
        ("research", lambda: __import__("models.research", fromlist=["get_research_engine"]).get_research_engine()),
        ("decisions", lambda: __import__("models.decision", fromlist=["get_decision_engine"]).get_decision_engine()),
        ("mf_analytics", lambda: __import__("models.mf_analytics", fromlist=["get_mf_analytics_engine"]).get_mf_analytics_engine()),
        ("scheduler", lambda: __import__("scheduler.store", fromlist=["get_scheduler_engine"]).get_scheduler_engine()),
        ("alerts", lambda: __import__("alerts.store", fromlist=["get_alerts_engine"]).get_alerts_engine()),
        ("usage", lambda: __import__("research.usage", fromlist=["get_usage_engine"]).get_usage_engine()),
    ):
        try:
            fn()
            created.append(label)
        except Exception as exc:  # noqa: BLE001
            print(f"[migrate] {label} provisioning failed: {exc}")
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.migrate")
    parser.add_argument("revision", nargs="?", default="head")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    try:
        from alembic.config import Config

        from alembic import command

        cfg = Config(str(root / "alembic.ini"))
        command.upgrade(cfg, args.revision)
        print(f"[migrate] alembic upgraded to {args.revision}")
    except Exception as exc:  # noqa: BLE001
        print(f"[migrate] alembic upgrade failed: {exc}")
        return 1

    provisioned = _provision_standalone()
    print(f"[migrate] provisioned standalone DBs: {provisioned}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
