"""Tests for deterministic MF performance & risk analytics (Stage 7)."""
from __future__ import annotations

import datetime as dt

from mf import analytics


def _series(annual_growth, n_days=1100, start=100.0, base=dt.date(2021, 1, 1)):
    return [
        ((base + dt.timedelta(days=i)).isoformat(), start * (1 + annual_growth) ** (i / 365.0))
        for i in range(n_days)
    ]


def test_to_series_sorted_and_deduped():
    s = analytics.to_series([("2021-01-02", 100), ("2021-01-01", 99), ("2021-01-01", 99.5), ("bad", 1)])
    assert s[0][1] == 99.5  # last value for duplicate date
    assert len(s) == 2


def test_trailing_return_approx():
    s = analytics.to_series(_series(0.10))
    tr = analytics.trailing_return(s, 1)
    assert tr is not None
    assert abs(tr - 0.10) < 0.01


def test_trailing_returns_keys():
    s = analytics.to_series(_series(0.10))
    tr = analytics.trailing_returns(s)
    assert set(tr) == {"1y", "3y", "5y"}


def test_max_drawdown_detects_dip():
    base = dt.date(2021, 1, 1)
    vals = [100, 110, 90, 95, 120]
    s = analytics.to_series([((base + dt.timedelta(days=i)).isoformat(), v) for i, v in enumerate(vals)])
    dd = analytics.max_drawdown(s)
    # peak 110 -> trough 90 => -18.18%
    assert dd is not None
    assert abs(dd["max_drawdown"] - (90 / 110 - 1)) < 1e-6


def test_volatility_and_sharpe_deterministic():
    s = analytics.to_series(_series(0.08))
    rets = analytics.periodic_returns(s)
    v1 = analytics.annualized_volatility(rets)
    v2 = analytics.annualized_volatility(rets)
    assert v1 == v2  # deterministic
    assert analytics.sharpe_ratio(rets) == analytics.sharpe_ratio(rets)


def test_downside_capture_and_tracking_error():
    # fund returns half of benchmark on both up and down days
    fund = [0.01, -0.02, 0.03, -0.01]
    bench = [0.02, -0.04, 0.06, -0.02]
    cap = analytics.downside_capture(fund, bench)
    assert cap is not None and abs(cap - 0.5) < 1e-9
    te = analytics.tracking_error(fund, bench)
    assert te is not None and te >= 0


def test_summarize_fund_with_benchmark_fields():
    fund = _series(0.12)
    bench = _series(0.10)
    summ = analytics.summarize_fund(fund, bench)
    assert summ["observations"] > 0
    assert "trailing_returns" in summ
    assert "excess_3y" in summ
    assert "benchmark_trailing_returns" in summ


def test_summarize_fund_empty():
    summ = analytics.summarize_fund([])
    assert summ["observations"] == 0
    assert summ["trailing_returns"]["1y"] is None
