"""Golden snapshot regression tests for Stage 4 SIP & Strategy Engine."""
from __future__ import annotations

import math
from datetime import date

from performance import stage4_signals as s4


# --------------------------------------------------------------------------- # helpers
def _stock(sid="STK", *, series=None, invested_cost=100000.0, avg_buy_price=100.0, xirr=0.20,
           mv=None, start=None, qty=None):
    series = series or []
    if mv is None and series:
        mv = (qty if qty is not None else 1.0) * series[-1]
    return s4.StockInput(
        security_id=sid, symbol=sid,
        quantity=qty if qty is not None else (invested_cost / avg_buy_price if avg_buy_price else 0.0),
        avg_buy_price=avg_buy_price, invested_cost=invested_cost, market_value=mv,
        xirr=xirr, holding_start_date=start, price_series=series,
    )


def _series_flat(n, price):
    return [float(price)] * n


def _series_rising(n, start=100.0, step=1.0):
    return [start + step * i for i in range(n)]


def _series_falling(n, start=300.0, step=1.0):
    return [start - step * i for i in range(n)]


# ---------------------------------------------------------------------------
# Golden invariants
# ---------------------------------------------------------------------------

EXCLUDED_TICKER = "NCD"  # Muthoot Finance NCD — never equity SIP
GTT_FLOOR_MIN_PCT = 0.55  # avg_buy_price * 0.55 (catastrophe 40% → 60% of cost)


def test_excluded_ticker_has_zero_sip():
    stocks = [_stock(sid="NCD", series=[90.0, 88.0], invested_cost=50000.0)]
    totals = s4.build_portfolio_totals(stocks, excluded_tickers={EXCLUDED_TICKER})
    assert totals.total_invested_cost == 0.0
    rows = s4.evaluate_signals([stocks[0]], totals, 0.12, excluded_tickers={EXCLUDED_TICKER})
    row = rows[0]
    assert row["security_id"] == "NCD"
    assert row["sip_multiplier"] == 0.0
    # recommended_sip_rupees no longer part of row schema


def test_gtt_catastrophe_floor_for_incubating():
    stocks = [_stock(sid="INC", series=[80.0]*30, invested_cost=90000.0, avg_buy_price=200.0)]
    totals = s4.build_portfolio_totals(stocks)
    metrics = s4.analyzeStockSignals(stocks[0], totals, 0.12)
    assert metrics.get("incubation") is True
    assert metrics.get("gtt_stop_loss_price") == round(stocks[0].avg_buy_price * 0.60, 2)
    assert metrics.get("primary_signal") != "LAGGARD_EXIT"


def test_gtt_cost_floor_graduated():
    stocks = [_stock(sid="GRD", series=[150.0]*50, invested_cost=80000.0, avg_buy_price=120.0)]
    totals = s4.build_portfolio_totals(stocks)
    metrics = s4.analyzeStockSignals(stocks[0], totals, 0.12)
    vol = metrics.get("annual_volatility")
    stop = metrics.get("dynamic_stop_loss_pct")
    expected_stop = max(0.20, min(0.45, 1.5 * vol))
    assert abs(metrics.get("dynamic_stop_loss_pct") - expected_stop) < 1e-6
    assert metrics.get("gtt_stop_loss_price") > stocks[0].avg_buy_price * 0.55


def test_hard_stop_overrides_warning():
    stock = _stock(sid="HARD", series=[100.0]*30, invested_cost=150000.0, avg_buy_price=100.0, xirr=-0.05, start=date(2023,1,1))
    totals = s4.build_portfolio_totals([stock])
    metrics = {
        "cmp": 100.0,
        "dma_50": 100.0,
        "dma_200": 100.0,
        "dynamic_stop_loss_pct": 0.20,
        "aer": 0.40,
        "cost_weight_pct": 0.10,
        "efficiency_score": -0.50,
    }
    sig = s4.evaluate_signal(stock, metrics, totals, as_of_date=date(2025,1,1))
    assert sig.signal_type == s4.HARD_STOP_LOSS
    assert sig.sip_multiplier == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])