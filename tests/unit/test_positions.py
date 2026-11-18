"""Tests for fully-sold position filtering."""
from __future__ import annotations

from src.positions import filter_fully_sold, fully_sold_isins, net_positions

ROWS = [
    {"isin": "A", "buy_sell": "BUY", "quantity": 10},
    {"isin": "A", "buy_sell": "SELL", "quantity": 10},
    {"isin": "B", "buy_sell": "BUY", "quantity": 5},
]


def test_net_positions():
    assert net_positions(ROWS) == {"A": 0.0, "B": 5.0}


def test_fully_sold_requires_zero_and_sell():
    assert fully_sold_isins(ROWS) == {"A"}


def test_baseline_prevents_false_closed():
    base = [{"isin": "A", "buy_sell": "BUY", "quantity": 1}]
    assert fully_sold_isins(ROWS, baseline=base) == set()


def test_filter_returns_only_closed_rows():
    kept, closed = filter_fully_sold(ROWS)
    assert closed == {"A"}
    assert all(r["isin"] == "A" for r in kept)
