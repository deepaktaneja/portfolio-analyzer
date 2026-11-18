"""Tests for dividend cashflow conversion used by XIRR."""

from __future__ import annotations

from app.shared import compute_dividend_cashflows

TXS = [
    {"isin": "INE1", "buy_sell": "BUY", "quantity": 10, "trade_date": "2024-01-01"},
    {"isin": "INE1", "buy_sell": "SELL", "quantity": 10, "trade_date": "2024-06-01"},
]

DIVIDENDS = [
    {"security_id": "INE1", "ex_date": "2024-03-01", "dividend_per_share": 5.0},
    {"security_id": "INE1", "ex_date": "2024-07-01", "dividend_per_share": 5.0},
    {"security_id": "INE2", "ex_date": "2024-03-01", "dividend_per_share": 7.0},
]


def test_uses_quantity_held_on_ex_date():
    flows = compute_dividend_cashflows(TXS, DIVIDENDS)
    assert len(flows) == 1
    assert flows[0]["security_id"] == "INE1"
    assert flows[0]["payment_date"] == "2024-03-01"
    assert flows[0]["gross"] == 50.0
    assert flows[0]["status"] == "confirmed"


def test_dividend_after_sell_is_excluded():
    flows = compute_dividend_cashflows(TXS, DIVIDENDS)
    dates = [f["payment_date"] for f in flows]
    assert "2024-07-01" not in dates


def test_no_transactions_no_flows():
    assert compute_dividend_cashflows([], DIVIDENDS) == []


def test_net_applies_30_percent_tax():
    flows = compute_dividend_cashflows(TXS, DIVIDENDS)
    assert len(flows) == 1
    assert flows[0]["gross"] == 50.0
    assert flows[0]["tax"] == 15.0
    assert flows[0]["net"] == 35.0


def test_custom_tax_rate():
    flows = compute_dividend_cashflows(TXS, DIVIDENDS, tax_rate=0.10)
    assert flows[0]["net"] == 45.0
