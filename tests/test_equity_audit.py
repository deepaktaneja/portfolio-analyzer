"""Equity audit tests for Stage 4 SIP allocation & GTT trigger upgrades.

Covers:
- All recommended SIPs are exact multiples of Rs 5,000 and sum to the deployable budget.
- Over-allocated stocks (AER >= 1.25 OR market weight >= 8%) receive Rs 0 SIP.
- gtt_stop_loss_price switches from Cost Floor to Trailing Peak Floor once the
  52-week peak >= 2.00 x avg_buy_price (+100% rally).
"""
from __future__ import annotations

import math
from datetime import date

from performance import stage4_signals as s4


# --------------------------------------------------------------------------- #
# Part 1: SIP rounding + over-allocation
# --------------------------------------------------------------------------- #
def test_sip_allocations_are_5k_multiples_summing_to_deployable():
    rows = [
        {"security_id": "A", "sip_multiplier": 2.0, "cmp": 100.0},
        {"security_id": "B", "sip_multiplier": 1.0, "cmp": 50.0},
        {"security_id": "C", "sip_multiplier": 1.0, "cmp": 200.0},
        {"security_id": "D", "sip_multiplier": 0.0, "cmp": 75.0},
    ]
    res = s4.compute_monthly_sip_allocation(rows, 297500.0)  # not a multiple of 5000
    deployable = res["deployable_budget"]
    assert deployable % 5000 == 0
    assert abs(res["carry_forward_cash"] - 2500.0) < 1e-6  # 297500 -> 295000, carry 2500

    total = 0.0
    for r in res["rows"]:
        sip = r["recommended_sip_rupees"]
        assert sip % 5000 == 0, f"{r['security_id']} SIP not a 5k multiple: {sip}"
        total += sip
    assert abs(total - deployable) < 1e-6, "SIPs must sum exactly to the deployable budget"

    d = next(r for r in res["rows"] if r["security_id"] == "D")
    assert d["recommended_sip_rupees"] == 0.0  # zero-multiplier stock gets nothing


def test_sip_est_shares_floor():
    rows = [{"security_id": "A", "sip_multiplier": 1.0, "cmp": 137.0}]
    res = s4.compute_monthly_sip_allocation(rows, 100000.0)
    r = res["rows"][0]
    assert r["recommended_sip_rupees"] == 100000.0
    assert r["est_shares"] == math.floor(100000.0 / 137.0)


def _stock(sid, *, series, invested, avg, mv, xirr=0.10, start=date(2020, 1, 1)):
    return s4.StockInput(
        security_id=sid, symbol=sid, quantity=invested / avg if avg else 0.0,
        avg_buy_price=avg, invested_cost=invested, market_value=mv,
        xirr=xirr, holding_start_date=start, price_series=series,
    )


def test_over_allocated_receives_zero_sip_and_no_warning():
    # AER = (mv/totalMV)/(cost/totalCost) = (300k/1M)/(100k/1M) = 3.0 -> over-allocated
    flat = [100.0] * 260
    stock = _stock("OVER", series=flat, invested=100000.0, avg=100.0, mv=300000.0)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    rows = s4.evaluate_signals([stock], totals, 0.12, as_of_date=date(2026, 11, 17))
    r = rows[0]
    assert r["over_allocated"] is True
    assert r["sip_multiplier"] == 0.0
    assert r["warning_level"] is False  # over-allocation is "let winners run", not a warning

    alloc = s4.compute_monthly_sip_allocation(rows, 300000.0)
    assert alloc["rows"][0]["recommended_sip_rupees"] == 0.0


def test_over_allocated_via_aer():
    # AER >= 1.35 (Market Weight >= 1.35 x Cost Weight) -> frozen; no fixed weight cap
    flat = [100.0] * 260
    # cost_w = 0.1, mkt_w = 0.14 -> AER = 1.40
    stock = _stock("HEAVY", series=flat, invested=100000.0, avg=100.0, mv=140000.0)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    rows = s4.evaluate_signals([stock], totals, 0.12, as_of_date=date(2026, 11, 17))
    r = rows[0]
    assert r["aer"] >= 1.35
    assert r["sip_multiplier"] == 0.0
    assert r["warning_level"] is False


# --------------------------------------------------------------------------- #
# Part 2: Cost Floor vs Trailing Peak Floor GTT
# --------------------------------------------------------------------------- #
def _rising(n=260, start=100.0, step=0.002):
    return [start * math.exp(step * i) for i in range(n)]


def test_gtt_cost_floor_when_no_rally():
    flat = [100.0] * 260
    stock = _stock("FLAT", series=flat, invested=200000.0, avg=100.0, mv=200000.0)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.12, as_of_date=date(2026, 11, 17))
    assert m["incubation"] is False
    assert m["gtt_anchor_type"] == "Cost Floor"
    stop_loss = m["dynamic_stop_loss_pct"]
    assert abs(m["gtt_stop_loss_price"] - 100.0 * (1.0 - stop_loss)) < 1e-6


def test_gtt_trailing_peak_floor_when_rally_over_100pct():
    series = _rising(260, 100.0, 0.003)  # ~217 peak -> >= 2.00 x 100 (+100% rally)
    peak = max(series[-s4.VOL_WINDOW:])
    assert peak >= 100.0 * 2.00
    stock = _stock("RALLY", series=series, invested=200000.0, avg=100.0, mv=200000.0)
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    m = s4.analyzeStockSignals(stock, totals, 0.12, as_of_date=date(2026, 11, 17))
    assert m["gtt_anchor_type"] == "Trailing Peak Floor"
    assert m["gtt_anchor_price"] is not None and m["gtt_anchor_price"] >= 100.0 * 2.00
    cost_floor = 100.0 * (1.0 - m["dynamic_stop_loss_pct"])
    assert m["gtt_stop_loss_price"] > cost_floor


def test_gtt_switches_anchor_between_cost_and_trailing():
    totals = s4.PortfolioTotals(total_invested_cost=1e6, total_market_value=1e6)
    cost_stock = _stock("C", series=[100.0] * 260, invested=200000.0, avg=100.0, mv=200000.0)
    trail_stock = _stock("T", series=_rising(260, 100.0, 0.003), invested=200000.0, avg=100.0,
                         mv=200000.0)
    mc = s4.analyzeStockSignals(cost_stock, totals, 0.12, as_of_date=date(2026, 11, 17))
    mt = s4.analyzeStockSignals(trail_stock, totals, 0.12, as_of_date=date(2026, 11, 17))
    assert mc["gtt_anchor_type"] == "Cost Floor"
    assert mt["gtt_anchor_type"] == "Trailing Peak Floor"
    assert mt["gtt_stop_loss_price"] > mc["gtt_stop_loss_price"]

