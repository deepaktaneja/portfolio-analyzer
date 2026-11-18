"""Scheduler configuration — loads ``config/scheduler.yaml`` merged over defaults.

Every job has: enabled, interval_hours, and an idempotency scope (daily/monthly/quarterly).
Defaults are conservative (daily refresh, monthly research) and fully user-editable.
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_PATH = CONFIG_DIR / "scheduler.yaml"

DEFAULT_SCHEDULE: dict = {
    "timezone": "Asia/Kolkata",
    "stale_run_timeout_minutes": 30,
    "jobs": {
        "refresh_prices": {"enabled": True, "interval_hours": 24, "scope": "daily"},
        "refresh_corporate_actions": {"enabled": True, "interval_hours": 24, "scope": "daily"},
        "refresh_research": {"enabled": True, "interval_hours": 720, "scope": "monthly"},
        "evaluate_alerts": {"enabled": True, "interval_hours": 6, "scope": "hourly"},
        "backup_databases": {"enabled": True, "interval_hours": 24, "scope": "daily"},
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_schedule(path: str | Path | None = None) -> dict:
    """Load the schedule config merged over defaults (safe if the file is absent)."""
    p = Path(path) if path else DEFAULT_PATH
    override: dict = {}
    if p.exists():
        try:
            override = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            override = {}
    cfg = _deep_merge(DEFAULT_SCHEDULE, override)
    cfg["jobs"] = {name: {**DEFAULT_SCHEDULE["jobs"].get(name, {}), **v}
                   for name, v in cfg.get("jobs", {}).items()}
    return cfg
