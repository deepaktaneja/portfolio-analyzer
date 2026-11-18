"""Config loading for Stage 9 rebalancing (YAML over defaults)."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_TARGETS_PATH = CONFIG_DIR / "allocation_targets.yaml"

DEFAULTS: dict = {
    "asset_classes": {
        "Equity": {"target": 65.0, "min": 50.0, "max": 80.0},
        "Debt": {"target": 25.0, "min": 15.0, "max": 40.0},
        "Gold": {"target": 5.0, "min": 0.0, "max": 15.0},
        "Alternatives": {"target": 5.0, "min": 0.0, "max": 15.0},
    },
    "sector_caps": {"Financials": 30.0, "Technology": 25.0, "Consumer": 20.0},
    "market_cap": {"Large": {"min": 50.0, "max": 80.0}, "Mid": {"min": 10.0, "max": 30.0}, "Small": {"min": 0.0, "max": 20.0}},
    "mf_vs_direct": {"mf_target": 40.0, "direct_target": 60.0},
    "rebalancing": {
        "strategy": "threshold", "drift_tolerance": 5.0, "min_trade_size": 10000,
        "max_turnover_pct": 30.0, "prioritize_cash": True, "prioritize_sip": True,
        "respect_decision_signals": True, "protect_compounders": True,
    },
    "tax": {
        "equity_stcg_rate": None, "equity_ltcg_rate": None, "equity_ltcg_exemption": None,
        "stt_rate": 0.001, "ltcg_holding_days": 365, "exit_load_default_pct": 0.0,
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


def load_targets(path: str | Path | None = None) -> dict:
    """Load allocation targets from YAML merged over defaults (safe if file absent)."""
    p = Path(path) if path else DEFAULT_TARGETS_PATH
    override: dict = {}
    if p.exists():
        try:
            override = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            override = {}
    return _deep_merge(DEFAULTS, override)
