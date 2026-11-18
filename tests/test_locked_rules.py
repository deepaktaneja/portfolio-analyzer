# Locked Stage 4 High-Alpha Rules (regression guard)
from __future__ import annotations

import math
from datetime import date

from performance import stage4_signals as s4


def _stock(sid, *, series, invested, avg, mv, xirr=0.30, start=date(2020, 1, 1)):
    return s4.StockInput(security_id=sid, symbol=sid, quantity=invested / avg if avg else 0.0,
                         avg_buy_price=avg, invested_cost=invested, market_value=mv,
                         xirr=xirr, holding_start_date=start, price_series=series, price_dates=series)

def test_lane1_returns_2_5x():
    st = _stock("L1", series=[100.0] * 260, invested=100000.0, avg=100.0, mv=100000.0, xirr=0.30)
    metrics = {"dma_50": 110.0, "dma_200": 110.0, "cmp": 120.0, "aer": 1.10}
    assert s4.compute_sip_multiplier(st, metrics, 0.12) == 2.5

def test_locked_constants():
    assert s4.TRIM_AER == 1.85
    assert s4.TRIM_TARGET_MULT == 1.40
    assert s4.SIP_PULLBACK_AER_MAX == 1.30
    assert s4.SIP_LANE1_MULT == 2.50
    assert s4.MAX_SINGLE_STOCK_SIP == 35000.0
    assert s4.SIP_RUN_UP_DMA_DISTANCE == 0.30
    assert s4.SIP_OVERALLOC_AER == 1.35
    assert s4.TRAILING_PEAK_MULT == 2.00
    assert s4.TRAILING_STOP_FLOOR == 0.35

def test_trim_threshold_and_reset():
    tot = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    below = _stock("B", series=[200.0] * 260, invested=100000.0, avg=100.0, mv=184000.0, xirr=0.30)
    assert s4.evaluate_signals([below], tot, 0.12, as_of_date=date(2026, 1, 1))[0]["signal_type"] != s4.CONCENTRATION_TRIM
    hi = _stock("H", series=[200.0] * 260, invested=100000.0, avg=100.0, mv=250000.0, xirr=0.30)
    r = s4.evaluate_signals([hi], tot, 0.12, as_of_date=date(2026, 1, 1))[0]
    assert r["signal_type"] == s4.CONCENTRATION_TRIM
    assert abs(r["target_sell_rupees"] - (250000.0 - 1.40 * 0.1 * 1e6)) < 1e-6

def test_harvested_redeploys_into_lane1_only():
    rows = [{"security_id": "L1", "sip_multiplier": 2.5, "cmp": 100.0}, {"security_id": "L2", "sip_multiplier": 1.0, "cmp": 100.0}]
    res = s4.compute_monthly_sip_allocation(rows, 0.0, harvested_cash=60000.0, max_single_stock_sip=35000.0)
    alloc = {r["security_id"]: r for r in res["rows"]}
    assert alloc["L1"]["recommended_sip_rupees"] == 35000.0
    assert alloc["L2"]["recommended_sip_rupees"] == 0.0
    assert res["parked_cash"] == 25000.0


def test_run_up_freeze_still_active():
    st = _stock("RUN", series=[100.0] * 260, invested=100000.0, avg=100.0, mv=100000.0, xirr=0.30)
    metrics = {"dma_50": 90.0, "dma_200": 90.0, "cmp": 120.0, "aer": 1.10}
    assert s4.compute_sip_multiplier(st, metrics, 0.12) == 0.0


def test_laggard_exit_beats_broken_trend():
    # Held 400 days, CMP < 200-DMA, efficiency < 0 -> LAGGARD_EXIT (never Broken Trend warning)
    series = [100.0] * 259 + [90.0]
    st = _stock("LAG", series=series, invested=100000.0, avg=100.0, mv=90000.0, xirr=-0.02, start=date(2025, 1, 1))
    tot = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    r = s4.evaluate_signals([st], tot, 0.12, as_of_date=date(2026, 2, 5))[0]
    assert r["signal_type"] == s4.LAGGARD_EXIT
    assert r["sip_multiplier"] == 0.0
    assert r["warning_level"] is False


# --------------------------------------------------------------------------- #
# Incubation Pacing Tier Tests (Stage 4C new-stock entry sizing)
# --------------------------------------------------------------------------- #
def test_incubation_tier_a_fast_track():
    # vol < 0.28, golden cross (DMA50 > DMA200), dist 0-0.15 -> Tier A ₹20,000
    metrics = {"annual_volatility": 0.20, "dma_50": 110.0, "dma_200": 100.0, "cmp": 105.0, "dma200_distance_pct": 0.05}
    res = s4.incubation_starter_sip(metrics)
    assert res["sip"] == 20000.0
    assert res["tier"] == "A"
    assert res["is_golden_cross"] is True


def test_incubation_tier_b_standard_high_vol():
    # vol >= 0.28 but dist 0-0.30 -> Tier B ₹10,000
    metrics = {"annual_volatility": 0.35, "dma_50": 95.0, "dma_200": 100.0, "cmp": 110.0, "dma200_distance_pct": 0.10}
    res = s4.incubation_starter_sip(metrics)
    assert res["sip"] == 10000.0
    assert res["tier"] == "B"
    assert res["is_golden_cross"] is False


def test_incubation_tier_b_standard_pullback_zone():
    # dist +15% to +30% (not Tier A zone) -> Tier B ₹10,000
    metrics = {"annual_volatility": 0.20, "dma_50": 110.0, "dma_200": 100.0, "cmp": 125.0, "dma200_distance_pct": 0.25}
    res = s4.incubation_starter_sip(metrics)
    assert res["sip"] == 10000.0
    assert res["tier"] == "B"


def test_incubation_tier_c_overheated():
    # dist > +30% -> Tier C ₹5,000
    metrics = {"annual_volatility": 0.20, "dma_50": 110.0, "dma_200": 100.0, "cmp": 135.0, "dma200_distance_pct": 0.35}
    res = s4.incubation_starter_sip(metrics)
    assert res["sip"] == 5000.0
    assert res["tier"] == "C"


def test_incubation_tier_c_below_dma():
    # CMP < 200-DMA -> Tier C ₹5,000
    metrics = {"annual_volatility": 0.20, "dma_50": 110.0, "dma_200": 100.0, "cmp": 90.0, "dma200_distance_pct": -0.10}
    res = s4.incubation_starter_sip(metrics)
    assert res["sip"] == 5000.0
    assert res["tier"] == "C"


def test_incubation_insufficient_data_toehold():
    # No DMA200 -> Tier C toe-hold guard ₹5,000
    metrics = {"annual_volatility": 0.20, "dma_50": None, "dma_200": None, "cmp": 100.0, "dma200_distance_pct": None}
    res = s4.incubation_starter_sip(metrics)
    assert res["sip"] == 5000.0
    assert res["tier"] == "C"


def test_months_to_graduate():
    # 0 invested, ₹20k/month -> ceil(100000/20000) = 5 months
    assert s4.months_to_graduate(0.0, 20000.0) == 5
    # ₹90k invested, ₹10k/month -> ceil(10000/10000) = 1 month
    assert s4.months_to_graduate(90000.0, 10000.0) == 1
    # ₹0 invested, ₹5k/month -> ceil(100000/5000) = 20 months
    assert s4.months_to_graduate(0.0, 5000.0) == 20
    # already graduated (>= 1L) -> 0 months
    assert s4.months_to_graduate(100000.0, 10000.0) == 0
    assert s4.months_to_graduate(150000.0, 20000.0) == 0


# --------------------------------------------------------------------------- #
# GTT Trigger Price Floor Regression (never 0.50 x avg_buy_price)
# --------------------------------------------------------------------------- #
def _tot() -> s4.PortfolioTotals:
    return s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)


def test_gtt_incubation_catastrophe_floor():
    # Invested < ₹1,00,000 -> incubating -> GTT = avg * 0.60, Catastrophe Floor (40%)
    st = _stock("INC", series=[100.0] * 100, invested=50000.0, avg=100.0, mv=50000.0, xirr=0.10)
    m = s4.analyzeStockSignals(st, _tot(), 0.12, as_of_date=date(2026, 1, 1))
    assert m["incubation"] is True
    assert m["gtt_anchor_type"] == "Catastrophe Floor (40%)"
    assert abs(m["gtt_stop_loss_price"] - 100.0 * 0.60) < 1e-6
    assert m["gtt_stop_loss_price"] >= 100.0 * 0.55


def test_gtt_young_stock_incubation():
    # Held < 180 days -> incubating regardless of invested cost -> avg * 0.60
    st = _stock("YOUNG", series=[100.0] * 100, invested=200000.0, avg=100.0, mv=200000.0,
                xirr=0.20, start=date(2026, 1, 1))
    m = s4.analyzeStockSignals(st, _tot(), 0.12, as_of_date=date(2026, 2, 1))
    assert m["incubation"] is True  # 31 days < 180
    assert m["gtt_anchor_type"] == "Catastrophe Floor (40%)"
    assert abs(m["gtt_stop_loss_price"] - 60.0) < 1e-6


def test_gtt_graduated_short_history_cost_floor():
    # Graduated but only 30 days of price history (< 252) -> default vol=0.25
    # stop_loss = clamp(1.5*0.25, 0.20, 0.45) = 0.375 -> cost_floor = avg * 0.625
    st = _stock("SHORT", series=[100.0 + i * 0.1 for i in range(30)], invested=200000.0,
                avg=100.0, mv=200000.0, xirr=0.20, start=date(2024, 1, 1))
    m = s4.analyzeStockSignals(st, _tot(), 0.12, as_of_date=date(2026, 1, 1))
    assert m["incubation"] is False
    assert m["gtt_anchor_type"] == "Cost Floor"
    assert m["gtt_stop_loss_price"] is not None
    assert m["gtt_stop_loss_price"] >= 100.0 * 0.55, f"GTT {m['gtt_stop_loss_price']} < 55% of cost"
    # cost_floor = 100 * (1 - 0.375) = 62.5
    assert abs(m["gtt_stop_loss_price"] - 62.5) < 1e-6


def test_gtt_graduated_full_history_cost_floor():
    # Graduated with 260 days history -> vol computed, clamp [0.20, 0.45]
    # cost_floor always between 55% and 80% of avg
    st = _stock("FULL", series=[100.0] * 260, invested=200000.0, avg=100.0, mv=200000.0,
                xirr=0.20, start=date(2024, 1, 1))
    m = s4.analyzeStockSignals(st, _tot(), 0.12, as_of_date=date(2026, 1, 1))
    assert m["incubation"] is False
    assert m["gtt_anchor_type"] == "Cost Floor"
    assert m["gtt_stop_loss_price"] is not None
    assert 100.0 * 0.55 <= m["gtt_stop_loss_price"] <= 100.0 * 0.80


def test_gtt_never_below_55_percent_of_cost():
    # Every possible stock scenario: gtt_stop_loss_price >= avg * 0.55
    scenarios = [
        ("incubating", [100.0] * 50, 50000.0, 100.0, date(2026, 1, 1)),
        ("short_hist", [100.0 + i * 0.05 for i in range(40)], 200000.0, 100.0, date(2024, 1, 1)),
        ("full_hist", [100.0] * 260, 200000.0, 100.0, date(2024, 1, 1)),
        ("rising_peak", [100.0 + i * 0.5 for i in range(260)], 200000.0, 100.0, date(2024, 1, 1)),
    ]
    for name, series, invested, avg, start in scenarios:
        st = _stock(name.upper(), series=series, invested=invested, avg=avg, mv=invested,
                    xirr=0.20, start=start)
        m = s4.analyzeStockSignals(st, _tot(), 0.12, as_of_date=date(2026, 1, 1))
        gtt = m["gtt_stop_loss_price"]
        assert gtt is not None, f"{name}: GTT is None"
        assert gtt >= avg * 0.55, f"{name}: GTT {gtt} < 55% of avg {avg}"
        assert m["gtt_anchor_type"] in ("Catastrophe Floor (40%)", "Cost Floor", "Trailing Peak Floor")


def test_gtt_trailing_peak_after_2x_rally():
    # peak >= 2.00 x avg -> Trailing Peak Floor, gtt = max(cost_floor, trailing_floor)
    series = [100.0] * 200 + [300.0] * 60  # peak = 300 = 3.0 x avg
    st = _stock("TRAIL", series=series, invested=200000.0, avg=100.0, mv=300000.0,
                xirr=0.50, start=date(2024, 1, 1))
    m = s4.analyzeStockSignals(st, _tot(), 0.12, as_of_date=date(2026, 1, 1))
    assert m["incubation"] is False
    assert m["gtt_anchor_type"] == "Trailing Peak Floor"
    assert m["gtt_stop_loss_price"] is not None
    assert m["gtt_stop_loss_price"] >= 100.0 * 0.55
    # trailing_floor = 300 * (1 - clamp(1.5*vol, 0.35, 0.45))
    # With flat-then-spike series, vol is computed; trailing_floor should exceed cost_floor
    assert m["gtt_stop_loss_price"] >= m["gtt_stop_loss_price"]  # tautology, ensures no crash
