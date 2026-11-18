"""Alert preferences: per-type enablement, channels, cadence and cooldown.

Loaded from ``config/alerts.yaml`` merged over safe defaults. External channels
(email/webhook) are stubs only — nothing is ever sent without explicit approval.
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_PATH = CONFIG_DIR / "alerts.yaml"

_DEFAULT = {
    "enabled": True,
    "channels": ["in_app"],
    "cooldown_minutes": 360,
    "frequency": "immediate",  # immediate | daily | weekly
    "min_severity": "INFO",
}

DEFAULTS: dict = {
    "enabled": True,
    "default": dict(_DEFAULT),
    "types": {
        "SIGNAL_EXIT": {"cooldown_minutes": 60, "min_severity": "INFO"},
        "SIGNAL_TRIM": {"cooldown_minutes": 120, "min_severity": "WARN"},
        "SIGNAL_MONITOR": {"enabled": False},
        "CONCENTRATION": {"cooldown_minutes": 720, "min_severity": "WARN"},
        "VALUATION": {"cooldown_minutes": 720, "min_severity": "WARN"},
        "GOVERNANCE": {"cooldown_minutes": 720, "min_severity": "INFO"},
        "EARNINGS": {"cooldown_minutes": 720, "min_severity": "WARN"},
        "STALE_DATA": {"cooldown_minutes": 360, "min_severity": "WARN"},
        "IMPORT_FAILURE": {"cooldown_minutes": 360, "min_severity": "INFO"},
        "RECONCILIATION": {"cooldown_minutes": 720, "min_severity": "WARN"},
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


def load_preferences(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_PATH
    override: dict = {}
    if p.exists():
        try:
            override = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            override = {}
    return _deep_merge(DEFAULTS, override)


def resolve(prefs: dict | None, alert) -> dict:
    """Return effective delivery settings for an alert."""
    prefs = prefs or DEFAULTS
    merged = dict(prefs.get("default", _DEFAULT))
    merged.update((prefs.get("types") or {}).get(alert.alert_type, {}))
    merged["enabled"] = bool(prefs.get("enabled", True)) and bool(merged.get("enabled", True))
    merged["cooldown_seconds"] = int(float(merged.get("cooldown_minutes", 360)) * 60)
    return merged
