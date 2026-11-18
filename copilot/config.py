"""Copilot configuration (``config/copilot.yaml`` merged over safe defaults)."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_PATH = CONFIG_DIR / "copilot.yaml"

DEFAULTS: dict = {
    "llm": {
        "enabled": True,          # when False, copilot still answers deterministically
        "temperature": 0.1,
        "max_tokens": 1200,
        "narrate": True,          # use LLM to phrase an explanation around tool facts
        "intent_fallback": True,  # ask LLM to parse intent when rules are unsure
    },
    "history": {
        "max_turns": 20,
        "persist": False,
    },
    "security": {
        "max_input_chars": 2000,
        "rate_limit_per_minute": 30,
        "audit": True,
    },
    "tools": {
        "enabled": [
            "portfolio_summary", "holdings", "xirr", "allocation", "concentration",
            "gains", "dividends", "rebalance_plan", "trim_simulation", "overlap",
            "replacement_simulation", "signals", "explain_signal", "research_report",
        ],
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


def load_copilot_config(path: str | Path | None = None) -> dict:
    """Load copilot config merged over defaults (safe if the file is absent)."""
    p = Path(path) if path else DEFAULT_PATH
    override: dict = {}
    if p.exists():
        try:
            override = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            override = {}
    return _deep_merge(DEFAULTS, override)
