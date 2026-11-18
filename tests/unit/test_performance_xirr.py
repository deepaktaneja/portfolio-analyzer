from performance.xirr import (
    calculate_portfolio_snapshot,
    calculate_portfolio_twr,
    calculate_portfolio_xirr,
    calculate_stock_xirr,
    get_xirr_cashflow_audit,
    portfolio_value_series,
)

TX = [
    {"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0},
    {"trade_date": "2025-01-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 10, "net_amount": 1200.0},
]

def price_lookup(sid, d):
    return 120.0

def test_stock_xirr_basic():
    res = calculate_stock_xirr(TX, security_id="INE1", end_date="2025-01-01", price_lookup=price_lookup)
    assert res["xirr"] is not None
    labels = [c[2] for c in res["cashflows"] ]
    assert "buy" in labels and "sell" in labels

def test_stock_xirr_dividend():
    divs = [{"security_id": "INE1", "ex_date": "2024-06-01", "payment_date": "2024-06-15", "gross_amount": 50.0, "status": "confirmed"}]
    res = calculate_stock_xirr(TX, dividends=divs, security_id="INE1", end_date="2025-01-01", price_lookup=price_lookup)
    labels = [c[2] for c in res["cashflows"] ]
    assert "dividend" in labels

def test_stock_xirr_exited():
    res = calculate_stock_xirr(TX, security_id="INE1", end_date="2025-01-01", price_lookup=price_lookup)
    labels = [c[2] for c in res["cashflows"] ]
    assert "terminal_value" not in labels

def test_portfolio_xirr_custom_start():
    txs = [
        {"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0},
        {"trade_date": "2024-06-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 5, "net_amount": 600.0},
        {"trade_date": "2025-01-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 15, "net_amount": 2000.0},
    ]
    res = calculate_portfolio_xirr(txs, price_lookup=price_lookup, start_date="2024-06-01", end_date="2025-01-01")
    assert res["xirr"] is not None
    labels = [c[2] for c in res["cashflows"] ]
    assert "opening_value" in labels

def test_audit():
    res = get_xirr_cashflow_audit(TX, security_id="INE1", end_date="2025-01-01", price_lookup=price_lookup)
    assert isinstance(res, list)
    assert len(res) > 0

def test_twr():
    txs = [
        {"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0},
        {"trade_date": "2024-06-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 5, "net_amount": 600.0},
    ]
    r = calculate_portfolio_twr(txs, price_lookup=price_lookup, start_date="2024-01-01", end_date="2024-06-01")
    assert r is not None

def test_missing_price():
    def pl(sid, d): return None
    res = calculate_stock_xirr(TX, security_id="INE1", end_date="2025-01-01", price_lookup=pl)
    assert res["xirr"] is not None

def test_no_investment():
    res = calculate_stock_xirr([], security_id="INE1", end_date="2025-01-01", price_lookup=price_lookup)
    assert res["xirr"] is None


def test_portfolio_xirr_partial_terminal_uses_available_prices():
    pl = lambda sid, d: 100.0 if sid == "A" else None
    txs = [
        {"trade_date": "2024-01-01", "isin": "A", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0},
        {"trade_date": "2024-01-01", "isin": "B", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0},
    ]
    res = calculate_portfolio_xirr(txs, end_date="2024-12-31", price_lookup=pl, holdings_at_end={"A": 10, "B": 10})
    assert res["reason"] == "partial_terminal_price"
    assert res["xirr"] is not None
    assert res["missing_prices"] == ["B"]
    tv = [a for _, a, lab in res["cashflows"] if lab == "terminal_value"]
    assert tv and abs(tv[0] - 1000.0) < 1e-6

def test_portfolio_xirr_missing_all_terminal_is_not_reported():
    txs = [{"trade_date": "2024-01-01", "isin": "A", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    res = calculate_portfolio_xirr(txs, end_date="2024-12-31", price_lookup=lambda s, d: None, holdings_at_end={"A": 10})
    assert res["xirr"] is None
    assert res["reason"] in ("missing_terminal_price", "invalid_cashflows")
    assert res["missing_prices"] == ["A"]


def test_stock_xirr_reports_missing_terminal_security():
    txs = [{"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    res = calculate_stock_xirr(txs, security_id="INE1", end_date="2024-12-31", price_lookup=lambda s, d: None)
    assert res["xirr"] is None
    assert res["reason"] == "missing_terminal_price"
    assert res["missing_prices"] == ["INE1"]

def test_holdings_at_end_overrides_reconstruction():
    txs = [{"trade_date": "2024-01-01", "isin": "A", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    res = calculate_portfolio_xirr(txs, end_date="2024-12-31", price_lookup=lambda s, d: 100.0, holdings_at_end={"A": 20})
    tv = [a for _, a, lab in res["cashflows"] if lab == "terminal_value"]
    assert tv and abs(tv[0] - 2000.0) < 1e-6

def test_negative_reconstruction_clamped_to_zero():
    txs = [{"trade_date": "2024-01-01", "isin": "A", "buy_sell": "SELL", "quantity": 10, "net_amount": 1200.0}]
    res = calculate_portfolio_xirr(txs, end_date="2024-12-31", price_lookup=lambda s, d: 100.0)
    assert res["xirr"] is None


def test_portfolio_xirr_partial_opening_when_some_prices_missing():
    txs = [
        {"trade_date": "2024-01-01", "isin": "A", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0},
        {"trade_date": "2024-01-01", "isin": "B", "buy_sell": "BUY", "quantity": 5, "net_amount": 500.0},
    ]
    def pl(sid, d):
        return 120.0 if sid == "A" else None
    res = calculate_portfolio_xirr(txs, start_date="2024-06-01", end_date="2024-12-31", price_lookup=pl)
    flows = res.get("cashflows") or []
    opening = [a for _, a, lbl in flows if lbl == "opening_value"]
    assert opening and abs(opening[0] + 1200.0) < 1e-6


def test_portfolio_value_series_monthly_snapshots():
    txs = [{"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    series = portfolio_value_series(
        txs, price_lookup=lambda sid, d: 120.0, start_date="2024-01-01", end_date="2024-06-01"
    )
    # endpoints + Jan..May month-ends => 7 snapshots, each valued 10 * 120
    assert len(series) >= 5
    assert str(series[0][0]) == "2024-01-01"
    assert str(series[-1][0]) == "2024-06-01"
    assert all(abs(v - 1200.0) < 1e-6 for _, v in series)


def test_portfolio_value_series_no_trades():
    assert portfolio_value_series([], price_lookup=lambda sid, d: 100.0) == []


def test_portfolio_value_series_without_price_lookup():
    txs = [{"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    assert portfolio_value_series(txs, price_lookup=None) == []


def test_portfolio_value_series_missing_price_is_zero():
    txs = [{"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    series = portfolio_value_series(
        txs, price_lookup=lambda sid, d: None, start_date="2024-01-01", end_date="2024-03-01"
    )
    assert series
    assert all(v == 0.0 for _, v in series)


def test_portfolio_snapshot_basic():
    txs = [{"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    snap = calculate_portfolio_snapshot(txs, price_lookup=lambda sid, d: 150.0, as_of_date="2024-12-31")
    assert snap["holdings"] == {"INE1": 10.0}
    assert abs(snap["portfolio_value"] - 1500.0) < 1e-6
    assert abs(snap["invested_capital"] - 1000.0) < 1e-6
    assert abs(snap["gains"] - 500.0) < 1e-6
    assert snap["missing_prices"] == []


def test_portfolio_snapshot_before_first_buy_is_empty():
    txs = [{"trade_date": "2024-06-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    snap = calculate_portfolio_snapshot(txs, price_lookup=lambda sid, d: 150.0, as_of_date="2024-01-01")
    assert snap["holdings"] == {}
    assert snap["portfolio_value"] == 0.0
    assert snap["invested_capital"] == 0.0


def test_portfolio_snapshot_partial_sell_uses_fifo_cost():
    txs = [
        {"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0},
        {"trade_date": "2024-03-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 2000.0},
        {"trade_date": "2024-06-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 10, "net_amount": 1500.0},
    ]
    snap = calculate_portfolio_snapshot(txs, price_lookup=lambda sid, d: 200.0, as_of_date="2024-12-31")
    # FIFO consumes the 100/unit lot first, leaving the 200/unit lot => cost 2000.
    assert snap["holdings"]["INE1"] == 10.0
    assert abs(snap["invested_capital"] - 2000.0) < 1e-6
    assert abs(snap["portfolio_value"] - 2000.0) < 1e-6


def test_portfolio_snapshot_missing_price_flagged():
    txs = [{"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    snap = calculate_portfolio_snapshot(txs, price_lookup=lambda sid, d: None, as_of_date="2024-12-31")
    assert snap["holdings"] == {"INE1": 10.0}
    assert snap["portfolio_value"] == 0.0
    assert snap["missing_prices"] == ["INE1"]


def test_portfolio_snapshot_includes_opening_positions():
    ops = [{"security_id": "INE2", "ledger_opening_date": "2023-01-01", "opening_qty": 5, "original_acq_cost": 500.0}]
    snap = calculate_portfolio_snapshot(
        [], price_lookup=lambda sid, d: 150.0, as_of_date="2024-12-31", opening_positions=ops
    )
    assert snap["holdings"] == {"INE2": 5.0}
    assert abs(snap["invested_capital"] - 500.0) < 1e-6
    assert abs(snap["portfolio_value"] - 750.0) < 1e-6
