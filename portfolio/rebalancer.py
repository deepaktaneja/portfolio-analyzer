"""Rebalancing strategies & execution-plan builder (Stage 9)."""
from __future__ import annotations

from datetime import date

from portfolio import allocation
from portfolio.optimizer import plan_rebalance


def _to_date(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def should_rebalance(strategy: str, alloc_report: dict, drift_tolerance: float,
                     last_rebalance_date=None, today=None) -> dict:
    """Decide whether rebalancing is due under the chosen strategy."""
    strategy = (strategy or "threshold").lower()
    today = _to_date(today) or date.today()
    if strategy == "on_demand":
        return {"due": True, "reason": "On-demand rebalance requested."}
    if strategy == "annual":
        last = _to_date(last_rebalance_date)
        if last is None:
            return {"due": True, "reason": "No prior rebalance recorded — annual review due."}
        days = (today - last).days
        return {"due": days >= 365, "reason": f"{days} days since last rebalance (annual threshold 365)."}
    # threshold (default)
    drift = {r["asset_class"]: r["drift_pct"] for r in alloc_report.get("rows", [])}
    max_drift = max((abs(v) for v in drift.values()), default=0.0)
    breached = alloc_report.get("breached", [])
    if breached:
        return {"due": True, "reason": f"Allocation outside min/max bounds: {', '.join(breached)}."}
    return {
        "due": max_drift >= float(drift_tolerance),
        "reason": f"Max drift {max_drift:.1f}pp vs tolerance {float(drift_tolerance):.1f}pp.",
    }


def build_execution_plan(positions, targets, cash_available=0.0, signals=None, compounders=None,
                         lots_by_security=None, sell_date=None, last_rebalance_date=None,
                         today=None) -> dict:
    """Assemble allocation report + due check + tax-aware plan into one result."""
    report = allocation.allocation_report(positions, targets)
    rb_cfg = (targets or {}).get("rebalancing", {})
    due = should_rebalance(rb_cfg.get("strategy", "threshold"), report,
                           rb_cfg.get("drift_tolerance", 5.0), last_rebalance_date, today)
    plan = plan_rebalance(positions, targets, cash_available=cash_available, signals=signals,
                          compounders=compounders, lots_by_security=lots_by_security,
                          sell_date=sell_date, tax_config=(targets or {}).get("tax"))
    return {
        "allocation": report,
        "rebalance_due": due["due"],
        "due_reason": due["reason"],
        "plan": plan,
    }
