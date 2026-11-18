"""Tests for Stage 9 tax rules, capital-gains estimation & FIFO tax lots."""
from __future__ import annotations

from portfolio.tax import estimate_capital_gains, get_tax_rules
from portfolio.tax_lots import build_fifo_lots, estimate_lot_tax


def test_tax_rules_date_effective():
    before = get_tax_rules("2024-01-01")
    after = get_tax_rules("2025-01-01")
    assert before["stcg_rate"] == 0.15
    assert before["ltcg_rate"] == 0.10
    assert before["ltcg_exemption"] == 100_000.0
    assert after["stcg_rate"] == 0.20
    assert after["ltcg_rate"] == 0.125
    assert after["ltcg_exemption"] == 125_000.0


def test_tax_rules_overrides():
    rules = get_tax_rules("2025-01-01", {"equity_stcg_rate": 0.30})
    assert rules["stcg_rate"] == 0.30


def test_short_term_tax():
    out = estimate_capital_gains("2025-01-01", "2025-06-01", 100, 100, 120)
    assert out.is_long_term is False
    assert out.gain == 2000.0
    assert out.capital_gains_tax == 2000.0 * 0.20
    assert out.missing_cost_basis is False


def test_long_term_tax_with_exemption():
    # gain 600000 (qty 4000, +150); long-term; taxable = 600000-125000; 12.5%
    out = estimate_capital_gains("2022-01-01", "2026-01-01", 4000, 100, 250)
    assert out.is_long_term is True
    assert out.gain == 600000.0
    expected = (600000.0 - 125000.0) * 0.125
    assert abs(out.capital_gains_tax - expected) < 1e-6


def test_missing_cost_basis_flagged():
    out = estimate_capital_gains("2022-01-01", "2026-01-01", 100, None, 250)
    assert out.missing_cost_basis is True
    assert out.capital_gains_tax is None
    assert "Missing cost basis" in out.note


def test_loss_no_capital_gains_tax():
    out = estimate_capital_gains("2025-01-01", "2025-06-01", 100, 200, 150)
    assert out.gain == -5000.0
    assert out.capital_gains_tax == 0.0


def test_fifo_lots_and_consumption():
    txs = [
        {"isin": "X", "side": "BUY", "trade_date": "2022-01-01", "quantity": 100, "price": 100},
        {"isin": "X", "side": "BUY", "trade_date": "2023-01-01", "quantity": 100, "price": 200},
        {"isin": "X", "side": "SELL", "trade_date": "2024-01-01", "quantity": 120},
    ]
    lots = build_fifo_lots(txs, "X")
    # 20 remaining from the 2023 lot
    assert len(lots) == 1
    assert lots[0]["quantity"] == 80
    assert lots[0]["cost_per_unit"] == 200


def test_estimate_lot_tax():
    txs = [
        {"isin": "X", "side": "BUY", "trade_date": "2022-01-01", "quantity": 100, "price": 100},
    ]
    lots = build_fifo_lots(txs, "X")
    # Short-term sale (3 months) => taxable gain, so tax > 0.
    res = estimate_lot_tax(lots, "2022-04-01", 250, 100)
    assert res["covered_qty"] == 100
    assert res["missing_cost_basis"] is False
    assert res["estimated_tax"] > 0


def test_estimate_lot_tax_uncovered():
    res = estimate_lot_tax([], "2026-01-01", 250, 100)
    assert res["uncovered_qty"] == 100
    assert res["estimated_tax"] == 0.0
