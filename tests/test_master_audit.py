# Master Audit (final calibrated)
from __future__ import annotations

import math
from datetime import date

from performance import equity_analytics as ea
from performance import stage4_signals as s4



def _stock(sid, *, series, invested, avg, mv, xirr=0.10, start=date(2020, 1, 1)):
    return s4.StockInput(
        security_id=sid, symbol=sid,
        quantity=invested / avg if avg else 0.0,
        avg_buy_price=avg, invested_cost=invested, market_value=mv,
        xirr=xirr, holding_start_date=start, price_series=series,
        price_dates=series,
    )


def _tx(isin, side, d, qty, amt):
    return {"isin": isin, "buy_sell": side, "trade_date": d, "quantity": qty, "net_amount": amt}


def _rising(n=260, start=100.0, step=0.003):
    return [start * math.exp(step * i) for i in range(n)]


# ===== G1 Core Financial Math =====
def test_xirr_basic():
    t=[_tx("X","BUY","2025-01-01",100,100000.0),_tx("X","SELL","2026-01-01",100,120000.0)]
    assert abs(ea.stock_xirr(t,[],"X","2025-01-01","2026-01-01")-0.20)<1e-4

def test_xirr_multi():
    t=[_tx("X","BUY","2025-01-01",100,100000.0),_tx("X","BUY","2025-06-01",50,50000.0),_tx("X","SELL","2026-01-01",150,200000.0)]
    x=ea.stock_xirr(t,[],"X","2025-01-01","2026-01-01")
    assert x is not None and 0.35<x<0.45


def test_sold_off_vs_active():
    sold=[_tx("X","BUY","2025-01-01",100,100000.0),_tx("X","SELL","2025-06-01",100,120000.0)]
    xs=ea.stock_xirr(sold,[],"X","2025-01-01","2026-01-01")
    assert xs is not None and xs>0.5
    act=[_tx("X","BUY","2025-01-01",100,100000.0)]
    xa=ea.stock_xirr(act,[],"X","2025-01-01","2026-01-01",terminal_value=130000.0,terminal_date="2026-01-01")
    assert xa is not None and abs(xa-0.30)<0.01

def test_ta():
    p=list(range(1,51))
    assert s4._sma(p,50)==25.5 and s4._sma(p,100) is None
    assert s4._dynamic_stop_loss_pct(0.10)==0.20
    assert s4._dynamic_stop_loss_pct(0.40)==0.45


# ===== G2 Signal & Boundary Verification =====
def test_incubation_gate():
    st=_stock("INC",series=[70.0]*260,invested=99999.0,avg=100.0,mv=69993.0,xirr=0.05,start=date(2025,6,1))
    tot=s4.PortfolioTotals(total_invested_cost=1e6,total_market_value=1e6)
    r=s4.evaluate_signals([st],tot,0.12,as_of_date=date(2026,1,1))[0]
    assert r["incubation"] is True and r["sip_multiplier"]==1.0
    assert r["signal_type"] not in (s4.CONCENTRATION_TRIM,s4.LAGGARD_EXIT)
    assert abs(r["gtt_stop_loss_price"]-60.0)<1e-6

def test_graduation():
    st=_stock("GRAD",series=[100.0]*260,invested=100001.0,avg=100.0,mv=110000.0,xirr=0.20,start=date(2024,1,1))
    tot=s4.PortfolioTotals(total_invested_cost=1e6,total_market_value=1e6)
    r=s4.evaluate_signals([st],tot,0.12,as_of_date=date(2026,1,1))[0]
    assert r["incubation"] is False
    assert r["sip_multiplier"] in (0.0,0.5,1.0,2.0)

def test_hard_stop_guard():
    st=_stock("HS",series=[100.0]*260,invested=200000.0,avg=100.0,mv=80000.0,xirr=0.0)
    tot=s4.PortfolioTotals(total_invested_cost=1e6,total_market_value=1e6)
    r=s4.evaluate_signals([st],tot,0.10,as_of_date=date(2026,1,1))[0]
    assert r["signal_type"]==s4.HARD_STOP_LOSS
    assert r["warning_level"] is False and r["sip_multiplier"]==0.0

def test_overalloc_aer():
    st=_stock("OA",series=[100.0]*260,invested=100000.0,avg=100.0,mv=140000.0,xirr=0.20)
    tot=s4.PortfolioTotals(total_invested_cost=1e6,total_market_value=1e6)
    r=s4.evaluate_signals([st],tot,0.12,as_of_date=date(2026,1,1))[0]
    assert r["aer"]>=1.35 and r["sip_multiplier"]==0.0 and r["warning_level"] is False
    a=s4.compute_monthly_sip_allocation([r],300000.0)
    assert a["rows"][0]["recommended_sip_rupees"]==0.0

def test_gtt_floor_switch():
    tot=s4.PortfolioTotals(total_invested_cost=1e6,total_market_value=1e6)
    c=_stock("C",series=[100.0]*260,invested=200000.0,avg=100.0,mv=200000.0)
    assert s4.analyzeStockSignals(c,tot,0.12,as_of_date=date(2026,11,17))["gtt_anchor_type"]=="Cost Floor"
    t=_stock("T",series=_rising(260,100.0,0.003),invested=200000.0,avg=100.0,mv=200000.0)
    mt=s4.analyzeStockSignals(t,tot,0.12,as_of_date=date(2026,11,17))
    assert mt["gtt_anchor_type"]=="Trailing Peak Floor" and mt["gtt_anchor_price"]>=200.0

def test_trim_boundary():
    tot=s4.PortfolioTotals(total_invested_cost=1e6,total_market_value=1e6)
    lo=_stock("LO",series=[200.0]*260,invested=100000.0,avg=100.0,mv=149000.0,xirr=0.30)
    assert s4.evaluate_signals([lo],tot,0.12,as_of_date=date(2026,1,1))[0]["signal_type"]!=s4.CONCENTRATION_TRIM
    hi=_stock("HI",series=[200.0]*260,invested=100000.0,avg=100.0,mv=250000.0,xirr=0.30)
    r=s4.evaluate_signals([hi],tot,0.12,as_of_date=date(2026,1,1))[0]
    assert r["signal_type"]==s4.CONCENTRATION_TRIM and abs(r["target_sell_rupees"]-110000.0)<1e-6

def test_sip_no_cap_and_carry():
    rows=[{"security_id":"A","sip_multiplier":1.0,"cmp":100.0}]
    res=s4.compute_monthly_sip_allocation(rows,303500.0)
    assert res["deployable_budget"]==300000.0 and abs(res["carry_forward_cash"]-3500.0)<1e-6
    assert res["rows"][0]["recommended_sip_rupees"]==300000.0 and res["parked_cash"]==0.0


# ===== G3 SIP 5000 rounding + 25000 cap + park =====
def test_sip_cap_and_park():
    rows=[{"security_id":"A","sip_multiplier":2.5,"cmp":100.0},{"security_id":"B","sip_multiplier":1.0,"cmp":50.0},{"security_id":"C","sip_multiplier":1.0,"cmp":200.0}]
    res=s4.compute_monthly_sip_allocation(rows,300000.0,max_single_stock_sip=35000.0)
    tot=0.0
    for r in res["rows"]:
        s=r["recommended_sip_rupees"]
        assert s%5000==0 and s<=35000.0
        tot+=s
    assert res["parked_cash"]==300000.0-tot and res["parked_cash"]==195000.0

def test_weight_sums():
    st=[_stock("A",series=[100.0]*260,invested=100000.0,avg=100.0,mv=150000.0),_stock("B",series=[100.0]*260,invested=100000.0,avg=100.0,mv=50000.0)]
    tot=s4.build_portfolio_totals(st)
    rows=s4.evaluate_signals(st,tot,0.12,as_of_date=date(2026,1,1))
    assert abs(sum(r["cost_weight_pct"] for r in rows)-1.0)<1e-9
    assert abs(sum(r["market_weight_pct"] for r in rows)-1.0)<1e-9


# ===== G4 FIFO Capital Gains =====
def test_fifo_ltcg_stcg():
    tx=[{"isin":"Z","buy_sell":"BUY","trade_date":"2022-01-01","quantity":100,"net_amount":10000.0},{"isin":"Z","buy_sell":"BUY","trade_date":"2026-01-01","quantity":50,"net_amount":10000.0}]
    p=s4.fifo_tax_preview(tx,"Z",120,300.0,date(2026,11,17))
    assert p["ltcg_shares"]==100.0 and p["stcg_shares"]==20.0
    assert abs(p["ltcg_gain"]-20000.0)<1e-6 and abs(p["stcg_gain"]-2000.0)<1e-6
    assert abs(p["estimated_tax"]-400.0)<1e-6

def test_fifo_loss_no_tax():
    tx=[{"isin":"Z","buy_sell":"BUY","trade_date":"2022-01-01","quantity":100,"net_amount":30000.0}]
    p=s4.fifo_tax_preview(tx,"Z",100,200.0,date(2026,11,17))
    assert p["realized_gain"]<0 and p["estimated_tax"]==0.0

def test_fifo_negative_lot():
    tx=[{"isin":"X","buy_sell":"BUY","trade_date":"2022-01-01","quantity":100,"net_amount":10000.0},{"isin":"X","buy_sell":"SELL","trade_date":"2023-01-01","quantity":150,"net_amount":12000.0}]
    lots=s4.fifo_lots(tx,"X",date(2026,11,17))
    assert all(l["qty"]>=0 for l in lots) and sum(l["qty"] for l in lots)==0.0

def test_short_history():
    st=_stock("NEW",series=[100.0+i for i in range(45)],invested=50000.0,avg=100.0,mv=6000.0,xirr=0.10,start=date(2026,10,1))
    tot=s4.PortfolioTotals(total_invested_cost=1e6,total_market_value=1e6)
    r=s4.evaluate_signals([st],tot,0.12,as_of_date=date(2026,11,17))[0]
    assert r["incubation"] is True and r["sip_multiplier"]==1.0


# ===== G5 Chaos & Edge Cases =====
def test_broad_crash():
    dec=[300.0-(200.0/259.0)*i for i in range(260)]
    sts=[_stock("C"+str(i),series=dec,invested=100000.0,avg=200.0,mv=90000.0,xirr=0.05) for i in range(5)]
    tot=s4.build_portfolio_totals(sts)
    rows=s4.evaluate_signals(sts,tot,0.12,as_of_date=date(2026,1,1))
    assert all(r["sip_multiplier"]==0.0 for r in rows)
    a=s4.compute_monthly_sip_allocation(rows,300000.0)
    assert a["all_frozen"] is True and a["parked_cash"]==300000.0

def test_empty_portfolio():
    tot=s4.PortfolioTotals(total_invested_cost=0.0,total_market_value=0.0)
    assert s4.evaluate_signals([],tot,0.12,as_of_date=date(2026,1,1))==[]
    a=s4.compute_monthly_sip_allocation([],300000.0)
    assert a["all_frozen"] is True and a["deployable_budget"]==300000.0 and a["parked_cash"]==300000.0

def test_reentered_stock():
    tx=[{"isin":"X","buy_sell":"BUY","trade_date":"2023-01-02","quantity":100,"net_amount":10000.0},{"isin":"X","buy_sell":"SELL","trade_date":"2024-01-02","quantity":100,"net_amount":12000.0},{"isin":"X","buy_sell":"BUY","trade_date":"2026-10-18","quantity":50,"net_amount":10000.0}]
    q,c,a,s=s4._fifo_position(tx,"X",date(2026,11,17))
    assert q==50.0 and s==date(2026,10,18)
