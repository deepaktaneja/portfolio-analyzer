"""Tests for Stage 9 UI glue helpers (position building, compounder detection)."""
from __future__ import annotations

import pandas as pd

from portfolio import ui


def test_equity_positions_from_dataframe():
    df = pd.DataFrame([{
        "isin": "INE001A01001", "symbol": "ABC", "current_value": 1000.0,
        "quantity": 10, "avg_price": 50, "current_price": 100,
        "sector": "Technology", "market_cap": "Large",
    }])
    pos = ui._equity_positions(df)
    assert len(pos) == 1
    assert pos[0]["security_id"] == "INE001A01001"
    assert pos[0]["value"] == 1000.0
    assert pos[0]["asset_class"] == "Equity"
    assert pos[0]["is_mf"] is False


def test_equity_positions_empty():
    assert ui._equity_positions(None) == []
    assert ui._equity_positions(pd.DataFrame()) == []


def test_compounders_detects_strong_appreciators():
    df = pd.DataFrame([
        {"isin": "A", "quantity": 10, "avg_price": 50, "current_value": 1000},   # +100%
        {"isin": "B", "quantity": 10, "avg_price": 100, "current_value": 1100},  # +10%
    ])
    s = ui._compounders(df)
    assert "A" in s
    assert "B" not in s


def test_mf_positions_uses_mf_context(monkeypatch):
    from app import mf_ui

    monkeypatch.setattr(mf_ui, "get_mf_context", lambda ctx: {
        "has_mf_data": True,
        "scheme_rows": [
            {"scheme_code": "123", "scheme_name": "HDFC Gold ETF Fund", "current_value": 5000.0, "nav": 25.0},
            {"scheme_code": "456", "scheme_name": "ICICI Liquid Fund", "current_value": 3000.0, "nav": 30.0},
        ],
    })
    pos = ui._mf_positions(object())
    assert len(pos) == 2
    assert pos[0]["asset_class"] == "Gold"
    assert pos[1]["asset_class"] == "Debt"
    assert pos[0]["is_mf"] is True
