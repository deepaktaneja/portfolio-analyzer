"""Scenario comparison for rebalancing (Stage 9).

Compares current portfolio vs the proposed post-rebalance portfolio on allocation
drift, concentration, expected return and cost/tax — deterministic.
"""
from __future__ import annotations

from portfolio import allocation


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def project_class_values(positions: list[dict], actions: list[dict]) -> dict[str, float]:
    """Projected asset-class values after applying BUY/SELL actions."""
    cur = allocation.aggregate_by(positions, "asset_class")
    for a in actions or []:
        cls = a.get("asset_class")
        amt = _num(a.get("amount"))
        if a.get("action") == "BUY":
            cur[cls] = cur.get(cls, 0.0) + amt
        elif a.get("action") == "SELL":
            cur[cls] = max(0.0, cur.get(cls, 0.0) - amt)
    return cur


def _pct(values: dict[str, float]) -> dict[str, float]:
    total = sum(values.values())
    return {k: round(v / total * 100, 2) for k, v in values.items()} if total > 0 else {}


def expected_return(weights_pct: dict[str, float], expected_returns_by_class: dict | None) -> float | None:
    """Weighted expected portfolio return (fraction) from class weights + class expected returns."""
    if not expected_returns_by_class:
        return None
    acc = 0.0
    used = 0.0
    for cls, w in weights_pct.items():
        r = expected_returns_by_class.get(cls)
        if r is not None:
            acc += (w / 100.0) * float(r)
            used += w
    return acc if used > 0 else None


def compare_scenarios(positions, targets, plan, expected_returns_by_class=None) -> dict:
    """Compare current vs proposed allocation with drift, concentration, return, cost/tax."""
    positions = allocation.normalize_positions(positions)
    actions = (plan or {}).get("actions", [])
    cur_classes = allocation.aggregate_by(positions, "asset_class")
    proj_classes = project_class_values(positions, actions)
    cur_pct = _pct(cur_classes)
    proj_pct = _pct(proj_classes)

    ac_targets = (targets or {}).get("asset_classes", {})
    def drift_of(pct):
        return {c: round(pct.get(c, 0.0) - float(cfg.get("target", 0.0)), 2) for c, cfg in ac_targets.items()}
    drift_cur = drift_of(cur_pct)
    drift_proj = drift_of(proj_pct)

    concentration_cur = allocation.concentration_metrics(positions)
    # approximate projected positions (reduce sold, add synthetic class buys)
    proj_positions = []
    for p in positions:
        p2 = dict(p)
        for a in actions:
            if a.get("action") == "SELL" and a.get("security_id") == p["security_id"]:
                p2["value"] = max(0.0, _num(p2.get("value")) - _num(a.get("amount")))
        proj_positions.append(p2)
    for a in actions:
        if a.get("action") == "BUY":
            proj_positions.append({"security_id": f"__buy_{a.get('asset_class')}__", "value": _num(a.get("amount")),
                                   "asset_class": a.get("asset_class"), "is_mf": False, "sector": None, "market_cap": None})
    concentration_proj = allocation.concentration_metrics(proj_positions)

    return {
        "current": {
            "weights_pct": cur_pct, "drift_pct": drift_cur,
            "concentration": concentration_cur,
            "expected_return": expected_return(cur_pct, expected_returns_by_class),
        },
        "proposed": {
            "weights_pct": proj_pct, "drift_pct": drift_proj,
            "concentration": concentration_proj,
            "expected_return": expected_return(proj_pct, expected_returns_by_class),
        },
        "costs": {
            "estimated_tax": (plan or {}).get("estimated_tax", 0.0),
            "sells_value": (plan or {}).get("sells_value", 0.0),
            "buys_value": (plan or {}).get("buys_value", 0.0),
            "turnover_pct": (plan or {}).get("turnover_pct", 0.0),
        },
        "max_abs_drift_before": max((abs(v) for v in drift_cur.values()), default=0.0),
        "max_abs_drift_after": max((abs(v) for v in drift_proj.values()), default=0.0),
    }


def compare_three_way(positions, targets, cash_available, plan_full, plan_cash_only=None,
                      expected_returns_by_class=None) -> dict:
    """High-level trade-off: do-nothing vs invest-cash-only vs full rebalance."""
    do_nothing = compare_scenarios(positions, targets, {"actions": []}, expected_returns_by_class)
    cash_only = compare_scenarios(positions, targets, plan_cash_only or {"actions": []}, expected_returns_by_class)
    full = compare_scenarios(positions, targets, plan_full, expected_returns_by_class)
    return {
        "do_nothing": do_nothing,
        "invest_cash": cash_only,
        "rebalance": full,
        "cash_available": _num(cash_available),
    }
