"""Tests for cumulative benchmark comparison series."""
from __future__ import annotations

from app.pages.portfolio import _cumulative_series


def test_cumulative_series_compounds():
    rows = [
        {
            "period": "2020",
            "portfolio_value": 1000.0,
            "nifty50_tri_return": 0.05,
            "nifty500_tri_return": 0.04,
        },
        {
            "period": "2021",
            "portfolio_value": 1320.0,
            "nifty50_tri_return": 0.10,
            "nifty500_tri_return": 0.08,
        },
    ]
    xs, ypf, y50, y500, has50, has500 = _cumulative_series(rows)
    assert xs == ["2020", "2021"]
    # Portfolio is a value-growth index anchored at the first value (0 -> +32%).
    assert ypf == [0.0, 32.0]
    # Benchmarks compound their calendar-year TRI returns.
    assert y50 == [5.0, 15.5]
    assert y500 == [4.0, 12.32]
    assert has50 and has500


def test_cumulative_series_handles_missing():
    rows = [
        {
            "period": "2020",
            "portfolio_value": 1100.0,
            "nifty50_tri_return": None,
            "nifty500_tri_return": None,
        }
    ]
    xs, ypf, y50, y500, has50, has500 = _cumulative_series(rows)
    # First portfolio value anchors the index at 0.
    assert xs == ["2020"]
    assert ypf == [0.0]
    # Missing benchmark returns yield None (no line drawn) and flag the series absent.
    assert y50 == [None]
    assert y500 == [None]
    assert not has50 and not has500
