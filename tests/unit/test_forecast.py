"""Tests for the forward-looking forecast engine.

Tests bear/base/bull scenario generation and deterministic calculations.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from research.forecast import (
    ForecastEngine,
    GrowthAssumptions,
    ScenarioProjection,
    ValuationScenario,
    ForwardAnalysis,
)


# ---------------------------------------------------------------------------
# Deterministic growth assumptions
# ---------------------------------------------------------------------------

def test_growth_assumptions_defaults():
    a = GrowthAssumptions()
    assert a.revenue_growth_y1 == Decimal("0.10")
    assert a.pe_multiple_base == Decimal("20")
    assert a.pe_multiple_bear == Decimal("15")
    assert a.pe_multiple_bull == Decimal("25")


def test_growth_assumptions_to_dict():
    a = GrowthAssumptions(revenue_growth_y1=Decimal("0.15"))
    d = a.to_dict()
    assert d["revenue_growth_y1"] == 0.15


# ---------------------------------------------------------------------------
# Forecast engine tests
# ---------------------------------------------------------------------------

def test_forecast_engine_analyzes_with_data():
    engine = ForecastEngine()
    result = engine.analyze(
        symbol="RELIANCE",
        current_price=2500.0,
        historical={"revenue": 100000, "eps": 85.0, "operating_margin": 0.14},
        analyst_consensus={"eps_estimate_y1": 95.0},
    )
    assert result.symbol == "RELIANCE"
    assert result.current_price == Decimal("2500")
    assert result.current_revenue == Decimal("100000")
    assert result.current_eps == Decimal("85")
    assert result.analyst_consensus_eps_y1 == Decimal("95")
    assert len(result.scenarios) == 3
    assert {s.scenario for s in result.scenarios} == {"bear", "base", "bull"}
    assert result.data_tier_notes[0].startswith("Analyst consensus EPS Y1")


def test_forecast_engine_handles_missing_data():
    engine = ForecastEngine()
    result = engine.analyze(symbol="RELIANCE", current_price=100.0)
    assert result.current_revenue is None
    assert result.current_eps is None
    assert result.data_tier_notes[0].startswith("No historical revenue")


def test_forecast_engine_deterministic():
    """Same input → same output (no randomness)."""
    engine = ForecastEngine()
    r1 = engine.analyze(
        symbol="TCS",
        current_price=3000.0,
        historical={"revenue": 50000, "eps": 50.0, "operating_margin": 0.20},
        analyst_consensus={"eps_estimate_y1": 60.0},
    )
    r2 = engine.analyze(
        symbol="TCS",
        current_price=3000.0,
        historical={"revenue": 50000, "eps": 50.0, "operating_margin": 0.20},
        analyst_consensus={"eps_estimate_y1": 60.0},
    )
    d1 = r1.to_dict()
    d2 = r2.to_dict()
    assert d1 == d2


# ---------------------------------------------------------------------------
# Scenario output tests
# ---------------------------------------------------------------------------

def test_scenario_projection_fields():
    p = ScenarioProjection(
        scenario="base", year=3, revenue=Decimal("120000"),
        eps=Decimal("90"), operating_margin=Decimal("0.15"),
        net_income=Decimal("2000"), free_cash_flow=Decimal("1600"),
    )
    assert p.scenario == "base"
    assert p.year == 3
    assert p.revenue == Decimal("120000")
    assert p.eps == Decimal("90")


def test_valuation_scenario_fields():
    v = ValuationScenario(
        scenario="base", pe_multiple=Decimal("20"),
        projected_eps_y3=Decimal("90"), estimated_price_y3=Decimal("1800"),
        current_price=Decimal("1500"),
        expected_return_3y_pct=Decimal("20"), expected_return_cagr_pct=Decimal("6.3"),
    )
    assert v.scenario == "base"
    assert v.pe_multiple == Decimal("20")
    assert v.estimated_price_y3 == Decimal("1800")
    assert v.current_price == Decimal("1500")