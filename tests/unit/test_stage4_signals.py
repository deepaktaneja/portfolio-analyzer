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
# Priority 3: CONCENTRATION_TRIM
# --------------------------------------------------------------------------- #
def test_concentration_trim_target_and_shares():
    stock = _stock(series=_series_rising(260, 90.0, 0.05), invested=100000.0, avg=100.0,
                   mv=250000.0, xirr=0.30)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.10)
    assert abs(m["aer"] - 2.5) < 1e-9  # 0.25/0.1
    sig = s4.evaluate_signal(stock, m, totals)
    assert sig.signal_type == s4.CONCENTRATION_TRIM
    cmp = stock.cmp
    assert abs(sig.target_sell_rupees - 110000.0) < 1e-6  # 250000 - 1.40*0.1*1e6
    assert sig.shares_to_sell == math.floor(110000.0 / cmp)


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
    base = {"dma_50": 110.0, "dma_200": 110.0}  # cmp 120 -> +9.1% above 200-DMA
    # Lane 1 2.0x Resting Winner: xirr>bench+0.10, 0<=dist<=0.15, aer<1.25
    st = _stock(xirr=0.30)
    assert s4.compute_sip_multiplier(st, {**base, "cmp": 120.0, "aer": 1.10}, 0.12) == 2.5
    # Lane 2 1.0x Cruising: 0<=dist<=0.30, 0.85<=aer<1.35 (xirr low so not 2x)
    st = _stock(xirr=0.10)
    assert s4.compute_sip_multiplier(st, {**base, "cmp": 120.0, "aer": 0.95}, 0.12) == 1.0
    # Lane 2 1.0x: distance in [0.15, 0.30] -> not 2x, still 1x
    st = _stock(xirr=0.30)
    assert s4.compute_sip_multiplier(st, {"dma_50": 100.0, "dma_200": 100.0, "cmp": 120.0, "aer": 1.10}, 0.12) == 1.0
    # Lane 3 0.0x Run-Up Freeze: distance > +30%
    st = _stock(xirr=0.30)
    assert s4.compute_sip_multiplier(st, {"dma_50": 90.0, "dma_200": 90.0, "cmp": 120.0, "aer": 1.10}, 0.12) == 0.0
    # Lane 3 0.0x Over-Allocated Freeze: AER >= 1.35
    st = _stock(xirr=0.30)
    assert s4.compute_sip_multiplier(st, {**base, "cmp": 120.0, "aer": 1.40}, 0.12) == 0.0
    # Lane 4 0.0x Broken Trend: cmp < 200-DMA
    st = _stock(xirr=0.30)
    assert s4.compute_sip_multiplier(st, {"dma_50": 130.0, "dma_200": 130.0, "cmp": 120.0, "aer": 1.10}, 0.12) == 0.0
    # Lane 4 0.0x Broken Trend: AER < 0.85
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



# --------------------------------------------------------------------------- #
# FIFO tax preview + live-override parameters (Stage 4B)
# --------------------------------------------------------------------------- #
def test_fifo_tax_preview_ltcg_stcg_split():
    txns = [
        {"isin": "Z", "buy_sell": "BUY", "trade_date": "2022-01-01", "quantity": 100, "net_amount": 10000},
        {"isin": "Z", "buy_sell": "BUY", "trade_date": "2026-01-01", "quantity": 50, "net_amount": 10000},
    ]
    p = s4.fifo_tax_preview(txns, "Z", 120, 300.0, date(2026, 11, 17))
    # oldest 100 lots (LTCG) + 20 from newer lot (STCG)
    assert p["ltcg_shares"] == 100.0
    assert p["stcg_shares"] == 20.0
    assert abs(p["ltcg_gain"] - 20000.0) < 1e-6      # (300-100)*100
    assert abs(p["stcg_gain"] - 2000.0) < 1e-6       # (300-200)*20
    assert abs(p["estimated_tax"] - 400.0) < 1e-6    # LTCG under 1.25L -> 0; STCG 2000*20%
    assert abs(p["net_cash_unlocked"] - 35600.0) < 1e-6


def test_fifo_tax_preview_ltcg_exemption():
    txns = [
        {"isin": "Z", "buy_sell": "BUY", "trade_date": "2022-01-01", "quantity": 1000, "net_amount": 100000},
    ]
    p = s4.fifo_tax_preview(txns, "Z", 1000, 300.0, date(2026, 11, 17))
    # LTCG gain = (300-100)*1000 = 200000; tax = (200000-125000)*12.5% = 9375
    assert abs(p["realized_gain"] - 200000.0) < 1e-6
    assert abs(p["estimated_tax"] - 9375.0) < 1e-6


def test_fifo_tax_preview_loss_no_tax():
    txns = [
        {"isin": "Z", "buy_sell": "BUY", "trade_date": "2022-01-01", "quantity": 100, "net_amount": 30000},
    ]
    p = s4.fifo_tax_preview(txns, "Z", 100, 200.0, date(2026, 11, 17))  # buy 300, sell 200
    assert p["realized_gain"] < 0
    assert p["estimated_tax"] == 0.0


def test_analyze_stop_loss_mult_override():
    prices = [100.0]
    for r in ([0.01, -0.01] * 130):
        prices.append(prices[-1] * math.exp(r))
    stock = _stock(series=prices)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m1 = s4.analyzeStockSignals(stock, totals, 0.10, stop_loss_mult=1.5)
    m2 = s4.analyzeStockSignals(stock, totals, 0.10, stop_loss_mult=2.5)
    assert m2["dynamic_stop_loss_pct"] > m1["dynamic_stop_loss_pct"]
    assert 0.20 <= m1["dynamic_stop_loss_pct"] <= 0.45
    assert 0.20 <= m2["dynamic_stop_loss_pct"] <= 0.45


def test_evaluate_signal_trim_aer_override():
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    stock2 = _stock(series=_series_rising(260, 90.0, 0.05), invested=100000.0, avg=100.0,
                    mv=140000.0, xirr=0.30)
    m2 = s4.analyzeStockSignals(stock2, totals, 0.10)  # AER = 1.4
    assert 1.35 <= m2["aer"] < 1.50
    sig_def = s4.evaluate_signal(stock2, m2, totals)  # default trim_aer=2.20 -> no trim
    sig_low = s4.evaluate_signal(stock2, m2, totals, trim_aer=1.30, min_market_value=100000.0)
    assert sig_def.signal_type != s4.CONCENTRATION_TRIM
    assert sig_low.signal_type == s4.CONCENTRATION_TRIM


# --------------------------------------------------------------------------- #
# Stage 4C: GTT protection, warning level, monthly SIP allocation
# --------------------------------------------------------------------------- #
def test_incubation_gtt_price():
    stock = _stock(series=_series_flat(260, 100.0), invested=50000.0, avg=100.0, mv=100000.0)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.10, as_of_date=date(2026, 1, 1))
    assert m["incubation"] is True            # invested < 100000
    assert abs(m["gtt_stop_loss_price"] - 60.0) < 1e-6   # avg * 0.60
    assert abs(m["gtt_buffer_pct"] - 0.40) < 1e-6        # (100-60)/100


def test_age_based_incubation():
    stock = _stock(series=_series_flat(260, 100.0), invested=200000.0, avg=100.0,
                   mv=210000.0, start=date(2025, 10, 1))
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.10, as_of_date=date(2026, 1, 1))  # ~92 days
    assert m["incubation"] is True


def test_graduated_gtt_price():
    prices = [100.0]
    for r in ([0.01, -0.01] * 130):
        prices.append(prices[-1] * math.exp(r))
    stock = _stock(series=prices, invested=200000.0, avg=100.0, mv=250000.0,
                   start=date(2020, 1, 1))
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.10, as_of_date=date(2026, 1, 1))
    assert m["incubation"] is False
    stop = m["dynamic_stop_loss_pct"]
    assert abs(m["gtt_stop_loss_price"] - 100.0 * (1.0 - stop)) < 1e-6


def test_warning_level_flag():
    series = [200.0] * 250 + [190.0]
    stock = _stock("W", series=series, invested=120000.0, avg=100.0, mv=100000.0,
                   xirr=0.0, start=date(2025, 3, 1))
    # Large portfolio totals so market weight (1%) stays under the 8% cap -> not over-allocated
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=10e6)
    rows = s4.evaluate_signals([stock], totals, 0.10, as_of_date=date(2026, 1, 1))
    r = rows[0]
    assert r["incubation"] is False
    assert r["signal_type"] != s4.HARD_STOP_LOSS
    assert r["over_allocated"] is False
    assert r["sip_multiplier"] == 0.0
    assert r["warning_level"] is True


def test_incubating_below_200dma_gets_half_x():
    # Incubating but below 200-DMA -> 0.5x toe-hold (trend not confirmed)
    series = _series_falling(260, 300.0, 0.5)
    stock = _stock("INC", series=series, invested=50000.0, avg=200.0, mv=40000.0,
                   xirr=0.0, start=date(2025, 12, 1))
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    rows = s4.evaluate_signals([stock], totals, 0.10, as_of_date=date(2026, 1, 1))
    r = rows[0]
    assert r["incubation"] is True
    assert r["sip_multiplier"] == 0.0


def test_incubating_healthy_gets_one_x():
    # Incubating with a healthy (flat / at-or-above 200-DMA) trend -> 1.0x
    series = _series_flat(260, 100.0)
    stock = _stock("INC2", series=series, invested=50000.0, avg=100.0, mv=50000.0,
                   xirr=0.0, start=date(2025, 12, 1))
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    rows = s4.evaluate_signals([stock], totals, 0.10, as_of_date=date(2026, 1, 1))
    r = rows[0]
    assert r["incubation"] is True
    assert r["sip_multiplier"] == 1.0


def test_compute_monthly_sip_allocation():
    rows = [
        {"security_id": "A", "sip_multiplier": 2.0, "cmp": 100.0},
        {"security_id": "B", "sip_multiplier": 1.0, "cmp": 50.0},
        {"security_id": "C", "sip_multiplier": 1.0, "cmp": 200.0},
    ]
    res = s4.compute_monthly_sip_allocation(rows, 300000.0)
    assert res["total_multiplier_units"] == 4.0
    assert res["deployable_budget"] == 300000.0
    alloc = {r["security_id"]: r for r in res["rows"]}
    assert abs(alloc["A"]["recommended_sip_rupees"] - 150000.0) < 1e-6  # 2/4 * 300k
    assert abs(alloc["B"]["recommended_sip_rupees"] - 75000.0) < 1e-6   # 1/4 * 300k
    assert alloc["A"]["est_shares"] == 1500  # 150000 / 100
    assert alloc["B"]["est_shares"] == 1500  # 75000 / 50


def test_monthly_sip_allocation_all_frozen():
    rows = [{"security_id": "A", "sip_multiplier": 0.0, "cmp": 100.0}]
    res = s4.compute_monthly_sip_allocation(rows, 300000.0)
    assert res["all_frozen"] is True
    assert res["rows"][0]["recommended_sip_rupees"] == 0.0


def test_monthly_sip_allocation_harvested_cash():
    # Harvested cash routes only into Lane 1 (2.5x) winners up to their cap; excess parks.
    rows = [{"security_id": "L1", "sip_multiplier": 2.5, "cmp": 100.0},
            {"security_id": "L2", "sip_multiplier": 1.0, "cmp": 100.0}]
    res = s4.compute_monthly_sip_allocation(rows, 0.0, harvested_cash=60000.0,
                                            max_single_stock_sip=35000.0)
    alloc = {r["security_id"]: r for r in res["rows"]}
    assert res["deployable_budget"] == 60000.0
    assert alloc["L1"]["recommended_sip_rupees"] == 35000.0
    assert alloc["L2"]["recommended_sip_rupees"] == 0.0
    assert res["parked_cash"] == 25000.0


def test_hard_stop_overrides_warning_level():
    # HARD_STOP_LOSS via AER collapse (0.4 <= 0.50) must force warning_level False
    stock = _stock("HS", series=_series_flat(260, 100.0), invested=200000.0, avg=100.0,
                   mv=80000.0, xirr=0.0, start=date(2020, 1, 1))
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    rows = s4.evaluate_signals([stock], totals, 0.10, as_of_date=date(2026, 1, 1))
    r = rows[0]
    assert r["signal_type"] == s4.HARD_STOP_LOSS
    assert r["sip_multiplier"] == 0.0
    assert r["warning_level"] is False



def test_incubation_old_stock_after_sell_below_cost():
    # Held > 365 days (>1 year) with remaining cost below ₹1L after partial sell -> NOT incubating,
    # should fall through to LAGGARD_EXIT / hard-stop evaluation instead of fresh ₹10k SIP.
    series = [80.0] * 30
    st = _stock("OLD", series=series, invested=60000.0, avg=100.0, mv=60000.0, xirr=-0.02, start=date(2023,1,1))
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(st, totals, 0.12, as_of_date=date(2026, 2, 1))
    assert m["incubation"] is False   # days > 365 → never incubating again
    row = s4.evaluate_signals([st], totals, 0.12, as_of_date=date(2026, 2, 1))[0]
    assert row["sip_multiplier"] == 0.0 or row["signal_type"] != "Incubating"


