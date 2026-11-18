"""MF quality assessment (Stage 7).

Produces an *explainable* quality assessment from computed analytics + metadata.
It flags funds that require further review but never recommends redemption.
All scoring is deterministic and rule-based (no randomness, no invented data).
"""
from __future__ import annotations


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _score_excess(excess_3y: float | None) -> tuple[float | None, str]:
    if excess_3y is None:
        return None, "Benchmark-relative return unavailable (needs 3y NAV + benchmark)."
    score = _clamp(55 + excess_3y / 0.05 * 45)
    if excess_3y >= 0.02:
        return score, f"3y return outpaced benchmark by {excess_3y*100:.1f}%."
    if excess_3y <= -0.02:
        return score, f"3y return lagged benchmark by {abs(excess_3y)*100:.1f}%."
    return score, f"3y return roughly in line with benchmark ({excess_3y*100:+.1f}%)."


def _score_sharpe(sharpe: float | None) -> tuple[float | None, str]:
    if sharpe is None:
        return None, "Sharpe ratio unavailable."
    score = _clamp(40 + sharpe * 35) if sharpe >= 0 else _clamp(40 + sharpe * 20)
    if sharpe >= 1.0:
        return score, f"Strong risk-adjusted return (Sharpe {sharpe:.2f})."
    if sharpe < 0:
        return score, f"Negative risk-adjusted return (Sharpe {sharpe:.2f})."
    return score, f"Moderate risk-adjusted return (Sharpe {sharpe:.2f})."


def _score_consistency(consistency: float | None) -> tuple[float | None, str]:
    if consistency is None:
        return None, "Performance consistency unavailable."
    score = _clamp(consistency * 100)
    if consistency >= 0.6:
        return score, f"Beat benchmark in {consistency*100:.0f}% of rolling 1y windows."
    if consistency < 0.4:
        return score, f"Beat benchmark in only {consistency*100:.0f}% of rolling 1y windows."
    return score, f"Beat benchmark in {consistency*100:.0f}% of rolling 1y windows."


def _score_cost(expense_ratio: float | None) -> tuple[float | None, str]:
    if expense_ratio is None:
        return None, "Expense ratio unavailable."
    er = float(expense_ratio)
    score = _clamp(100 - (er - 0.3) / (2.0 - 0.3) * 70)
    if er <= 0.6:
        return score, f"Low expense ratio ({er:.2f}%)."
    if er >= 1.5:
        return score, f"High expense ratio ({er:.2f}%)."
    return score, f"Moderate expense ratio ({er:.2f}%)."


def _score_concentration(overlap_max: float | None) -> tuple[float | None, str]:
    if overlap_max is None:
        return None, "Cross-fund overlap unavailable."
    score = _clamp(100 - overlap_max / 50.0 * 80)
    if overlap_max >= 40:
        return score, f"High overlap with another fund ({overlap_max:.0f}% weight overlap)."
    if overlap_max <= 10:
        return score, f"Low overlap with other funds ({overlap_max:.0f}%)."
    return score, f"Moderate overlap with other funds ({overlap_max:.0f}%)."


def _score_stability(manager_changes: int | None) -> tuple[float | None, str]:
    if manager_changes is None:
        return None, "Fund-manager history unavailable."
    score = _clamp(100 - manager_changes * 25)
    if manager_changes == 0:
        return score, "No recent fund-manager changes."
    return score, f"{manager_changes} fund-manager change(s) in the recent period."


_WEIGHTS = {
    "excess": 0.30,
    "sharpe": 0.25,
    "consistency": 0.15,
    "cost": 0.15,
    "concentration": 0.10,
    "stability": 0.05,
}


def assess_fund_quality(metrics: dict) -> dict:
    """Return an explainable quality assessment for one fund.

    ``metrics`` keys (all optional): excess_3y, sharpe_ratio, performance_consistency_1y,
    expense_ratio, overlap_max, manager_changes, max_drawdown, category, scheme_name.
    """
    components: dict[str, dict] = {}
    pairs = {
        "excess": _score_excess(metrics.get("excess_3y")),
        "sharpe": _score_sharpe(metrics.get("sharpe_ratio")),
        "consistency": _score_consistency(metrics.get("performance_consistency_1y")),
        "cost": _score_cost(metrics.get("expense_ratio")),
        "concentration": _score_concentration(metrics.get("overlap_max")),
        "stability": _score_stability(metrics.get("manager_changes")),
    }
    weighted_sum = 0.0
    weight_used = 0.0
    for key, (score, reason) in pairs.items():
        components[key] = {"score": round(score, 1) if score is not None else None, "reason": reason}
        if score is not None:
            weighted_sum += score * _WEIGHTS[key]
            weight_used += _WEIGHTS[key]

    overall = round(weighted_sum / weight_used, 1) if weight_used > 0 else None
    if overall is None:
        rating = "Insufficient data"
    elif overall >= 75:
        rating = "Strong"
    elif overall >= 55:
        rating = "Adequate"
    elif overall >= 40:
        rating = "Watch"
    else:
        rating = "Review"

    reasons = [c["reason"] for c in components.values() if c["score"] is not None]
    flags: list[str] = []
    if metrics.get("excess_3y") is not None and metrics["excess_3y"] <= -0.02:
        flags.append("Underperforms benchmark over 3y")
    if metrics.get("sharpe_ratio") is not None and metrics["sharpe_ratio"] < 0:
        flags.append("Negative risk-adjusted return")
    if metrics.get("expense_ratio") is not None and metrics["expense_ratio"] >= 1.5:
        flags.append("High expense ratio")
    if metrics.get("overlap_max") is not None and metrics["overlap_max"] >= 40:
        flags.append("High overlap with another fund")
    if metrics.get("manager_changes") is not None and metrics["manager_changes"] >= 2:
        flags.append("Multiple fund-manager changes")

    requires_review = (overall is not None and overall < 40) or len(flags) >= 2
    return {
        "scheme_code": metrics.get("scheme_code"),
        "scheme_name": metrics.get("scheme_name"),
        "overall_score": overall,
        "rating": rating,
        "components": components,
        "reasons": reasons,
        "flags": flags,
        "requires_review": requires_review,
        "disclaimer": "Advisory assessment only — not a redemption recommendation.",
    }