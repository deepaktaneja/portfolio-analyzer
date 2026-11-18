"""Tests for forward-return scenarios & tax estimation (Stage 8)."""
from __future__ import annotations

from decision.scenarios import expected_weighted_return, forward_scenarios, upside_downside
from decision.tax import estimate_tax_impact


def test_expected_weighted_return():
    scenarios = [
        {"scenario": "bear", "expected_return_cagr_pct": -10.0},
        {"scenario": "base", "expected_return_cagr_pct": 12.0},
        {"scenario": "bull", "expected_return_cagr_pct": 30.0},
    ]
    ev = expected_weighted_return(scenarios)
    # 0.25*-0.10 + 0.5*0.12 + 0.25*0.30 = 0.11
    assert abs(ev - 0.11) < 1e-9


def test_expected_weighted_return_empty():
    assert expected_weighted_return([]) is None


def test_upside_downside():
    scenarios = [
        {"scenario": "bear", "expected_return_cagr_pct": -10.0},
        {"scenario": "bull", "expected_return_cagr_pct": 30.0},
    ]
    up, down = upside_downside(scenarios)
    assert abs(up - 0.30) < 1e-9
    assert abs(down - (-0.10)) < 1e-9


def test_forward_scenarios_deterministic():
    hist = {"revenue": 100000, "eps": 85.0, "operating_margin": 0.14}
    analyst = {"eps_estimate_y1": 95.0, "eps_estimate_y2": 105.0}
    a = forward_scenarios("RELIANCE", 2500, hist, analyst)
    b = forward_scenarios("RELIANCE", 2500, hist, analyst)
    assert a["scenarios"] == b["scenarios"]
    assert a["expected_cagr_3y"] == b["expected_cagr_3y"]
    assert a["data_tier"] == "ai_forecast"


def test_tax_short_term():
    t = estimate_tax_impact("2026-01-01", "2026-06-01", 100, 100, 120)
    assert t.is_long_term is False
    assert t.estimated_gain == 2000.0
    assert t.estimated_tax == 2000.0 * 0.20


def test_tax_long_term_with_exemption():
    # quantity=100, gain/share=150 -> gain 15000; below 125000 exemption -> tax 0
    t = estimate_tax_impact("2023-01-01", "2026-01-01", 100, 100, 250)
    assert t.is_long_term is True
    assert t.estimated_gain == 15000.0
    assert t.estimated_tax == 0.0


def test_tax_loss_no_capital_gain_tax():
    t = estimate_tax_impact("2026-01-01", "2026-06-01", 100, 200, 150)
    assert t.estimated_gain == -5000.0
    assert t.estimated_tax == 0.0


def test_tax_insufficient_data():
    t = estimate_tax_impact(None, None, None, None, None)
    assert t.estimated_tax is None
    assert "Insufficient" in t.note
