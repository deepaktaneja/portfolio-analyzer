"""Decision-engine configuration: load user-editable YAML thresholds over defaults."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_THRESHOLDS_PATH = CONFIG_DIR / "decision_thresholds.yaml"

DEFAULTS: dict = {
    "equity": {
        "concentration": {"portfolio_pct_threshold": 12.0, "target_deviation_pct": 50.0},
        "valuation": {"forward_pe_percentile_threshold": 80, "peg_threshold": 2.0, "sector_pe_percentile": 90},
        "rapid_appreciation": {"price_gain_threshold": 0.80, "earnings_upgrade_threshold": 0.10, "window_months": 6},
        "fundamental": {"revenue_decline_qoq": -0.05, "eps_downgrade": -0.10, "margin_decline": -0.02, "fcf_decline": -0.15},
        "governance": {"promoter_pledging_threshold": 0.20, "auditor_concerns": True, "related_party_threshold_cr": 100.0},
        "forward_returns": {"expected_return_threshold": 0.08, "horizon_years": 3},
    },
    "mutual_fund": {
        "underperformance": {"benchmark_gap_1y": -0.05, "benchmark_gap_3y": -0.03},
        "risk_adjusted": {"sharpe_threshold": 0.0, "consistency_threshold": 0.40},
        "overlap": {"weight_overlap_threshold": 30.0, "jaccard_threshold": 0.4},
        "expenses": {"expense_ratio_threshold": 1.5},
        "manager": {"manager_changes_threshold": 2},
        "concentration": {"top5_threshold": 50.0, "single_stock_threshold": 15.0},
    },
    "engine": {
        "llm_enabled": True,
        "llm_required_for": ["EXIT_REVIEW", "REPLACEMENT_REVIEW"],
        "min_confidence_for_review": 0.55,
        "protect_compounders": True,
        "compounder_min_growth": 0.15,
        "governance_escalates_independently": True,
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


def load_thresholds(path: str | Path | None = None) -> dict:
    """Load thresholds from YAML, merged over built-in defaults (safe if file absent)."""
    p = Path(path) if path else DEFAULT_THRESHOLDS_PATH
    override: dict = {}
    if p.exists():
        try:
            override = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            override = {}
    return _deep_merge(DEFAULTS, override)


def get_equity_thresholds(config: dict | None = None) -> dict:
    return (config or load_thresholds())["equity"]


def get_mf_thresholds(config: dict | None = None) -> dict:
    return (config or load_thresholds())["mutual_fund"]


def get_engine_thresholds(config: dict | None = None) -> dict:
    return (config or load_thresholds())["engine"]
