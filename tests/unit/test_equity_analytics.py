"""Unit tests for performance.equity_analytics helpers."""
from __future__ import annotations

from performance import equity_analytics as ea


def test_normalize():
    assert ea.normalize([100, 110, 120]) == [100.0, 110.0, 120.0]
    assert ea.normalize([None, 50, 100]) == [None, 100.0, 200.0]


def test_alpha():
    assert ea.alpha(0.20, 0.10) == 0.1
    assert ea.alpha(None, 0.1) is None
    assert ea.alpha(0.1, None) is None


def test_total_dividends_period():
    flows = [
        {"payment_date": "2024-01-01", "gross": 100, "net": 70},
        {"payment_date": "2024-06-01", "gross": 50, "net": 35},
        {"payment_date": "2025-01-01", "gross": 30, "net": 21},
    ]
    assert ea.total_dividends(flows, "2024-01-01", "2024-12-31", "gross") == 150
    assert ea.total_dividends(flows, "2024-01-01", "2024-12-31", "net") == 105


def _mk_tx(isin, side, date, qty, amt):
    return {"isin": isin, "buy_sell": side, "trade_date": date, "quantity": qty, "net_amount": amt}


def test_stock_flows_signs():
    txs = [_mk_tx("ABC", "BUY", "2024-01-01", 10, 1000.0), _mk_tx("ABC", "SELL", "2024-06-01", 5, 800.0)]
    divs = [{"security_id": "ABC", "payment_date": "2024-03-01", "gross": 50.0}]
    flows = ea.stock_flows(txs, divs, "ABC", "2024-01-01", "2024-12-31", terminal_value=500.0, terminal_date="2024-12-31")
    amts = {label: round(amt, 2) for _, amt, label in flows}
    assert amts["buy"] == -1000.0
    assert amts["sell"] == 800.0
    assert amts["dividend"] == 50.0
    assert amts["terminal_value"] == 500.0


def test_stock_xirr_basic():
    txs = [_mk_tx("ABC", "BUY", "2024-01-01", 10, 1000.0), _mk_tx("ABC", "SELL", "2025-01-01", 10, 1200.0)]
    x = ea.stock_xirr(txs, [], "ABC", "2024-01-01", "2025-01-01")
    assert x is not None and x > 0


def test_stock_twr_price_only():
    txs = [_mk_tx("ABC", "BUY", "2024-01-01", 10, 1000.0)]
    prices = {"2024-01-01": 100.0, "2025-01-01": 110.0}
    def px(sid, d):
        return prices.get(str(d)[:10])
    tw = ea.stock_twr(txs, px, "ABC", "2024-01-01", "2025-01-01")
    assert tw is not None and abs(tw - 0.10) < 0.02


def test_day_change_pct():
    def px(sid, d):
        return 110.0 if str(d)[:10] == "2024-06-02" else 100.0
    ch = ea.day_change_pct(px, "ABC", "2024-06-02")
    assert ch is not None and abs(ch - 0.10) < 1e-6


def test_portfolio_flows_net():
    txs = [_mk_tx("A", "BUY", "2024-01-01", 10, 500.0), _mk_tx("B", "SELL", "2024-02-01", 5, 300.0)]
    flows = ea.portfolio_flows(txs, [], "2024-01-01", "2024-12-31")
    assert round(sum(a for _, a in flows), 2) == -200.0


def test_resolve_security():
    smap = {"RELIANCE.NS": "INE002A01018", "HDFCBANK.NS": "INE040A01034"}
    assert ea.resolve_security("RELIANCE", smap) == "INE002A01018"
    assert ea.resolve_security("reliance.ns", smap) == "INE002A01018"
    assert ea.resolve_security("INE002A01018", smap) == "INE002A01018"
    assert ea.resolve_security("UNKNOWN", smap) is None


def test_max_drawdown():
    series = [("d1", 100.0), ("d2", 120.0), ("d3", 90.0), ("d4", 110.0)]
    assert abs(ea._max_dd(series) - (-0.25)) < 1e-9


def test_simulate_lump_sum():
    prices = {"2024-01-01": 100.0, "2025-01-01": 200.0}
    def px(d):
        return prices.get(str(d)[:10], 100.0)
    r = ea.simulate_lump_sum(10000.0, px, "2024-01-01", "2025-01-01")
    assert round(r["invested"], 2) == 10000.0
    assert round(r["value_end"], 2) == 20000.0
    assert abs(r["abs_return"] - 1.0) < 1e-6


def test_simulate_sip_counts_installments():
    def px(d):
        return 100.0
    r = ea.simulate_sip(1000.0, px, "2024-01-01", "2024-06-30")
    assert r["installments"] == 6
    assert round(r["invested"], 2) == 6000.0


def test_simulate_cashflow_matched_alt_differs():
    txns = [{"isin": "A", "buy_sell": "BUY", "trade_date": "2024-01-01", "quantity": 10, "net_amount": 1000.0}, {"isin": "A", "buy_sell": "SELL", "trade_date": "2025-01-01", "quantity": 10, "net_amount": 1200.0}]
    a_prices = {"2024-01-01": 100.0, "2025-01-01": 120.0}
    b_prices = {"2024-01-01": 100.0, "2025-01-01": 150.0}
    def pxa(d):
        return a_prices.get(str(d)[:10], 100.0)
    def pxb(d):
        return b_prices.get(str(d)[:10], 100.0)
    res = ea.simulate_cashflow_matched(txns, pxa, pxb, "2025-01-01")
    assert res["actual"]["total_pnl"] < res["alternative"]["total_pnl"]
