"""Forward-return scenario analysis (Stage 8).

Wraps the deterministic Stage-6 forecast engine (`research/forecast.py`) and adds
probability-weighted expected returns. Clearly separates scenario forecasts
(AI-generated) from any historical facts.
"""
from __future__ import annotations

DEFAULT_WEIGHTS = {"bear": 0.25, "base": 0.50, "bull": 0.25}


def expected_weighted_return(scenarios: list[dict], weights: dict[str, float] | None = None) -> float | None:
    """Probability-weighted 3y CAGR from scenario dicts (each has 'scenario' and 'expected_return_cagr_pct')."""
    if not scenarios:
        return None
    w = weights or DEFAULT_WEIGHTS
    total_w = 0.0
    acc = 0.0
    for s in scenarios:
        name = s.get("scenario")
        val = s.get("expected_return_cagr_pct")
        if name in w and val is not None:
            acc += float(val) / 100.0 * w[name]
            total_w += w[name]
    return (acc / total_w) if total_w > 0 else None


def upside_downside(scenarios: list[dict]) -> tuple[float | None, float | None]:
    """Return (upside%, downside%) from bull/base/bear scenario CAGRs (fractions)."""
    by = {s.get("scenario"): s.get("expected_return_cagr_pct") for s in scenarios or []}
    bull = by.get("bull")
    bear = by.get("bear")
    upside = (float(bull) / 100.0) if bull is not None else None
    downside = (float(bear) / 100.0) if bear is not None else None
    return upside, downside


def forward_scenarios(symbol: str, current_price, historical: dict | None = None,
                      analyst_consensus: dict | None = None, assumptions=None,
                      weights: dict[str, float] | None = None) -> dict:
    """Build bear/base/bull scenarios + probability-weighted expected 3y CAGR."""
    from research.forecast import ForecastEngine

    engine = ForecastEngine(assumptions)
    analysis = engine.analyze(symbol, current_price, historical or {}, analyst_consensus or {})
    d = analysis.to_dict()
    scenarios = d.get("scenarios", [])
    expected = expected_weighted_return(scenarios, weights)
    upside, downside = upside_downside(scenarios)
    return {
        "symbol": symbol,
        "scenarios": scenarios,
        "expected_cagr_3y": expected,
        "upside_cagr": upside,
        "downside_cagr": downside,
        "assumptions": d.get("assumptions"),
        "data_tier": "ai_forecast",
        "weights": weights or DEFAULT_WEIGHTS,
    }
