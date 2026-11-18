"""Tests for portfolio-aware research integration.

Tests the PortfolioResearchIntegrator which combines portfolio accounting
(weight, invested capital, XIRR, unrealized gains) with company research.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from research.portfolio import (
    HoldingContext,
    PortfolioResearchContext,
    PortfolioResearchIntegrator,
)


# ---------------------------------------------------------------------------
# HoldingContext tests
# ---------------------------------------------------------------------------

def test_holding_context_creation():
    hc = HoldingContext(
        symbol="RELIANCE", isin="INE002A01018",
        quantity=Decimal("10"), avg_price=Decimal("2000"),
        invested_capital=Decimal("200000"),
        current_price=Decimal("2500"), current_value=Decimal("250000"),
        unrealized_gain=Decimal("50000"), unrealized_gain_pct=Decimal("25"),
        weight_pct=Decimal("10"), xirr=Decimal("0.15"), xirr_annualized=15.0,
    )
    assert hc.symbol == "RELIANCE"
    assert hc.invested_capital == Decimal("200000")
    assert hc.unrealized_gain == Decimal("50000")
    assert hc.weight_pct == Decimal("10")


# ---------------------------------------------------------------------------
# PortfolioResearchIntegrator tests
# ---------------------------------------------------------------------------

def test_portfolio_research_integrator_init_and_load():
    integrator = PortfolioResearchIntegrator(db_path=":memory:")
    assert integrator.db_path == ":memory:"
    assert integrator.portfolio_folder == "data/portfolio"


def test_load_transactions_mocked():
    integrator = PortfolioResearchIntegrator(db_path=":memory:")
    mock_store = MagicMock()
    mock_store.all_transactions.return_value = [
        {"trade_date": "2024-01-01", "buy_sell": "BUY", "net_amount": 100000, "isin": "INE002A01018"},
    ]
    with patch("research.portfolio.SQLiteStore", return_value=mock_store):
        txns = integrator.load_transactions()
        assert len(txns) == 1
        assert txns[0]["buy_sell"] == "BUY"


def test_portfolio_research_integrator_builds_holding_context():
    integrator = PortfolioResearchIntegrator(db_path=":memory:")
    mock_holdings = MagicMock()
    mock_holdings.empty = False
    mock_holdings.to_dict.return_value = [
        {
            "isin": "INE002A01018",
            "symbol": "RELIANCE",
            "quantity": 100,
            "avg_price": 2000,
            "current_value": 250000,
            "current_price": 2500,
        }
    ]
    with patch("research.portfolio.load_holdings", return_value=mock_holdings):
        holdings_data = integrator.load_portfolio_holdings()
        assert holdings_data, "load_portfolio_holdings should return mocked data"
        ctx = integrator.build_holding_context(
            symbol="RELIANCE",
            isin="INE002A01018",
            holdings_data=holdings_data,
            transactions=[],
            current_price_lookup=None,
        )
        assert ctx.symbol == "RELIANCE"
        assert ctx.quantity == Decimal("100")
        assert ctx.invested_capital == Decimal("200000")
        assert ctx.current_value == Decimal("250000")
        assert ctx.unrealized_gain == Decimal("50000")


def test_portfolio_research_integrator_aggregate_context():
    """build_portfolio_context aggregates holdings, computes weight, XIRR, risks."""
    integrator = PortfolioResearchIntegrator(db_path=":memory:")

    mock_holdings = MagicMock()
    mock_holdings.empty = False
    mock_holdings.to_dict.return_value = [
        {"isin": "INE002A01018", "symbol": "HIGHWEIGHT", "quantity": 10, "avg_price": 1000, "current_value": 20000, "current_price": 2000},
        {"isin": "INE002B01018", "symbol": "LOWWEIGHT", "quantity": 10, "avg_price": 1000, "current_value": 10000, "current_price": 1000},
    ]
    with patch.object(integrator, "load_portfolio_holdings", return_value=mock_holdings.to_dict.return_value), \
         patch.object(integrator, "load_transactions", return_value=[]):
        ctx = integrator.build_portfolio_context(
            symbols=["HIGHWEIGHT", "LOWWEIGHT"],
            isins={"HIGHWEIGHT": "INE002A01018", "LOWWEIGHT": "INE002B01018"},
            current_price_lookup=None,
        )
        assert len(ctx.holdings) == 2
        assert ctx.total_current_value == Decimal("30000")
        # HIGHWEIGHT is 66.7% of 30000 -> concentration flag
        symbols_flagged = {r["symbol"] for r in ctx.concentration_risk}
        assert "HIGHWEIGHT" in symbols_flagged