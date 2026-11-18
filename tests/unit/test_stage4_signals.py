"""Unit tests for performance.stage4_signals (pure math signal engine)."""
from __future__ import annotations

import math
import statistics
from datetime import date

from performance import stage4_signals as s4


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _stock(sid="X", *, series=None, invested=100000.0, avg=100.0, xirr=0.20,
           mv=None, start=None, qty=None):
    series = series or []
    if mv is None and series:
        mv = (qty if qty is not None else 1.0) * series[-1]
    return s4.StockInput(
        security_id=sid, symbol=sid,
        quantity=qty if qty is not None else (invested / avg if avg else 0.0),
        avg_buy_price=avg, invested_cost=invested, market_value=mv,
        xirr=xirr, holding_start_date=start, price_series=series,
    )


def _series_flat(n, price):
    return [float(price)] * n


def _series_rising(n, start=100.0, step=1.0):
    return [start + step * i for i in range(n)]


def _series_falling(n, start=300.0, step=1.0):
    return [start - step * i for i in range(n)]


# --------------------------------------------------------------------------- #
# numeric primitives
# --------------------------------------------------------------------------- #
def test_sma():
    assert s4._sma([1, 2, 3, 4, 5], 3) == 4.0
    assert s4._sma([1, 2], 3) is None
    assert s4._sma([], 3) is None


def test_daily_log_returns():
    prices = [100.0, 110.0, 99.0]
    rets = s4._daily_log_returns(prices)
    assert len(rets) == 2
    assert abs(rets[0] - math.log(1.10)) < 1e-12
    assert abs(rets[1] - math.log(0.90)) < 1e-12


def test_annualized_volatility_matches_definition():
    rets = [0.01, 0.02, -0.01, 0.015, -0.005, 0.0]
    expected = statistics.stdev(rets) * math.sqrt(252)
    assert abs(s4._annualized_volatility(rets) - expected) < 1e-12
    assert s4._annualized_volatility([0.01]) is None  # <2 points


def test_annualized_volatility_zero_for_constant_returns():
    assert s4._annualized_volatility([0.005] * 10) == 0.0


def test_dynamic_stop_loss_clamp():
    assert s4._dynamic_stop_loss_pct(0.10) == 0.20   # floor
    assert math.isclose(s4._dynamic_stop_loss_pct(0.20), 0.30)  # 1.5*0.20
    assert s4._dynamic_stop_loss_pct(0.40) == 0.45   # cap
    assert s4._dynamic_stop_loss_pct(None) is None


# --------------------------------------------------------------------------- #
# analyzeStockSignals
# --------------------------------------------------------------------------- #
def test_analyze_metrics_weights_aer_efficiency():
    stock = _stock(series=_series_flat(260, 100.0), invested=100.0, mv=150.0, xirr=0.20)
    totals = s4.PortfolioTotals(total_invested_cost=1000.0, total_market_value=1000.0)
    m = s4.analyzeStockSignals(stock, totals, benchmarkXIRR=0.10)
    assert abs(m["cost_weight_pct"] - 0.10) < 1e-9
    assert abs(m["market_weight_pct"] - 0.15) < 1e-9
    assert abs(m["aer"] - 1.5) < 1e-9
    assert abs(m["efficiency_score"] - 0.15) < 1e-9  # (0.20-0.10)*1.5
    assert m["dma_50"] == 100.0 and m["dma_200"] == 100.0
    assert m["insufficient_data"] is False


def test_analyze_insufficient_data_flag():
    stock = _stock(series=_series_flat(100, 50.0))
    totals = s4.PortfolioTotals(total_invested_cost=100.0, total_market_value=100.0)
    m = s4.analyzeStockSignals(stock, totals, 0.10)
    assert m["insufficient_data"] is True
    assert m["dma_200"] is None


def test_analyze_volatility_and_stop_loss():
    prices = [100.0]
    for r in ([0.01, -0.01] * 130):
        prices.append(prices[-1] * math.exp(r))
    stock = _stock(series=prices)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.10)
    assert m["annual_volatility"] is not None
    expected = min(0.45, max(0.20, 1.5 * m["annual_volatility"]))
    assert math.isclose(m["dynamic_stop_loss_pct"], expected)


# --------------------------------------------------------------------------- #
# Priority 1: HARD_STOP_LOSS
# --------------------------------------------------------------------------- #
def test_hard_stop_loss_via_price_drop():
    stock = _stock(series=_series_flat(260, 70.0), invested=200000.0, avg=100.0, mv=140000.0)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.10)
    sig = s4.evaluate_signal(stock, m, totals)
    assert sig.signal_type == s4.HARD_STOP_LOSS
    assert sig.sell_pct == 1.0


def test_hard_stop_loss_via_aer_collapse():
    stock = _stock(series=_series_flat(260, 100.0), invested=200000.0, avg=100.0, mv=80000.0)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.10)  # cost_w=0.2, mkt_w=0.08 -> AER=0.4
    sig = s4.evaluate_signal(stock, m, totals)
    assert sig.signal_type == s4.HARD_STOP_LOSS


def test_hard_stop_not_triggered_below_invested_threshold():
    stock = _stock(series=_series_flat(260, 100.0), invested=100000.0, avg=100.0, mv=40000.0)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.10)
    sig = s4.evaluate_signal(stock, m, totals)
    assert sig.signal_type != s4.HARD_STOP_LOSS


# --------------------------------------------------------------------------- #
# Priority 2: TREND_BREAK_LOCK
# --------------------------------------------------------------------------- #
def test_trend_break_lock():
    series = [200.0] * 60 + [180.0]  # last 180 < mean(~199.7)*0.97(~193.7)
    stock = _stock(series=series, invested=100000.0, avg=100.0, mv=180000.0)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.10)  # AER = 0.18/0.1 = 1.8
    assert m["aer"] >= 1.30
    sig = s4.evaluate_signal(stock, m, totals)
    assert sig.signal_type == s4.TREND_BREAK_LOCK
    assert sig.sell_pct == 0.5


# --------------------------------------------------------------------------- #
# Priority 3: CONCENTRATION_TRIM
# --------------------------------------------------------------------------- #
def test_concentration_trim_target_and_shares():
    stock = _stock(series=_series_rising(260, 90.0, 0.05), invested=100000.0, avg=100.0,
                   mv=200000.0, xirr=0.30)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.10)
    assert abs(m["aer"] - 2.0) < 1e-9  # 0.2/0.1
    sig = s4.evaluate_signal(stock, m, totals)
    assert sig.signal_type == s4.CONCENTRATION_TRIM
    cmp = stock.cmp
    assert abs(sig.target_sell_rupees - 75000.0) < 1e-6  # 200000 - 1.25*0.1*1e6
    assert sig.shares_to_sell == math.floor(75000.0 / cmp)


# --------------------------------------------------------------------------- #
# Priority 4: LAGGARD_EXIT
# --------------------------------------------------------------------------- #
def test_laggard_exit():
    stock = _stock(series=_series_falling(260, 300.0, 0.5), invested=100000.0, avg=200.0,
                   xirr=0.0, mv=90000.0, start=date(2024, 1, 1))
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.12)
    assert m["efficiency_score"] < 0
    assert stock.cmp < m["dma_200"]
    sig = s4.evaluate_signal(stock, m, totals, as_of_date=date(2026, 1, 1))
    assert sig.signal_type == s4.LAGGARD_EXIT


def test_laggard_not_triggered_within_one_year():
    stock = _stock(series=_series_falling(260, 300.0, 0.5), invested=100000.0, avg=200.0,
                   xirr=0.0, mv=90000.0, start=date(2025, 6, 1))
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.12)
    sig = s4.evaluate_signal(stock, m, totals, as_of_date=date(2026, 1, 1))
    assert sig.signal_type != s4.LAGGARD_EXIT



# --------------------------------------------------------------------------- #
# SIP multipliers
# --------------------------------------------------------------------------- #
def test_sip_multiplier_matrix():
    base = {"dma_50": 100.0, "dma_200": 95.0}
    st = _stock(xirr=0.30)
    assert s4.compute_sip_multiplier(st, {**base, "cmp": 120.0, "aer": 1.20}, 0.12) == 2.0
    st = _stock(xirr=0.10)
    assert s4.compute_sip_multiplier(st, {**base, "cmp": 120.0, "aer": 1.0}, 0.12) == 1.0
    st = _stock(xirr=0.10)
    assert s4.compute_sip_multiplier(st, {**base, "cmp": 120.0, "aer": 1.30}, 0.12) == 0.5
    st = _stock(xirr=0.30)
    assert s4.compute_sip_multiplier(st, {"dma_50": 100.0, "dma_200": 130.0,
                                          "cmp": 120.0, "aer": 1.20}, 0.12) == 0.0
    assert s4.compute_sip_multiplier(st, {**base, "cmp": 120.0, "aer": 0.5}, 0.12) == 0.0


def test_sip_multiplier_frozen():
    st = _stock(xirr=0.30)
    metrics = {"dma_50": 100.0, "dma_200": 95.0, "cmp": 120.0, "aer": 1.20}
    assert s4.compute_sip_multiplier(st, metrics, 0.12, frozen=True) == 0.0


# --------------------------------------------------------------------------- #
# FIFO position + holding_start_date reset on re-entry
# --------------------------------------------------------------------------- #
def test_fifo_position_reentry_resets_start():
    txns = [
        {"isin": "X", "buy_sell": "BUY", "trade_date": "2023-01-02", "quantity": 100, "net_amount": 10000},
        {"isin": "X", "buy_sell": "SELL", "trade_date": "2023-06-01", "quantity": 100, "net_amount": 12000},
        {"isin": "X", "buy_sell": "BUY", "trade_date": "2024-01-02", "quantity": 50, "net_amount": 10000},
    ]
    qty, cost, avg, start = s4._fifo_position(txns, "X", date(2025, 1, 1))
    assert qty == 50.0
    assert abs(cost - 10000.0) < 1e-6
    assert abs(avg - 200.0) < 1e-6
    assert start == date(2024, 1, 2)  # new episode after full exit


def test_fifo_partial_sell_keeps_episode_start():
    txns = [
        {"isin": "X", "buy_sell": "BUY", "trade_date": "2023-01-02", "quantity": 100, "net_amount": 10000},
        {"isin": "X", "buy_sell": "SELL", "trade_date": "2023-06-01", "quantity": 40, "net_amount": 4800},
    ]
    qty, _cost, _avg, start = s4._fifo_position(txns, "X", date(2025, 1, 1))
    assert qty == 60.0
    assert start == date(2023, 1, 2)


# --------------------------------------------------------------------------- #
# build_stock_input + portfolio totals
# --------------------------------------------------------------------------- #
def test_build_stock_input_and_totals():
    txns = [
        {"isin": "Y", "buy_sell": "BUY", "trade_date": "2024-01-02", "quantity": 50, "net_amount": 10000},
    ]
    series = [190.0, 195.0, 200.0, 210.0]
    si = s4.build_stock_input(txns, price_lookup=None, as_of_date=date(2025, 1, 1),
                              security_id="Y", series=series)
    assert si.quantity == 50.0
    assert abs(si.invested_cost - 10000.0) < 1e-6
    assert abs(si.avg_buy_price - 200.0) < 1e-6
    assert si.cmp == 210.0
    assert abs(si.market_value - 10500.0) < 1e-6
    assert si.holding_start_date == date(2024, 1, 2)

    totals = s4.build_portfolio_totals([si])
    assert abs(totals.total_invested_cost - 10000.0) < 1e-6
    assert abs(totals.total_market_value - 10500.0) < 1e-6


# --------------------------------------------------------------------------- #
# orchestrator + ranking
# --------------------------------------------------------------------------- #
def test_rank_bottom_laggards():
    rows = [
        {"security_id": "A", "efficiency_score": 0.2},
        {"security_id": "B", "efficiency_score": -0.3},
        {"security_id": "C", "efficiency_score": 0.0},
        {"security_id": "D", "efficiency_score": None},
        {"security_id": "E", "efficiency_score": -0.1},
    ]
    ranked = s4.rank_bottom_laggards(rows, n=2)
    assert [r["security_id"] for r in ranked] == ["B", "E"]


def test_evaluate_signals_flags_bottom_laggards_and_freezes_sip():
    stocks = [
        _stock("WIN", series=_series_rising(260, 90.0, 0.05), invested=100000.0, avg=100.0,
               mv=200000.0, xirr=0.30),
        _stock("LOSE", series=_series_falling(260, 300.0, 0.5), invested=100000.0, avg=200.0,
               xirr=0.0, mv=90000.0, start=date(2024, 1, 1)),
    ]
    totals = s4.build_portfolio_totals(stocks)
    rows = s4.evaluate_signals(stocks, totals, 0.10, as_of_date=date(2026, 1, 1))
    assert len(rows) == 2
    lose = next(r for r in rows if r["security_id"] == "LOSE")
    assert lose["is_bottom_laggard"] is True
    for r in rows:
        if r["signal_type"] in s4.FROZEN_SIGNALS:
            assert r["sip_multiplier"] == 0.0

