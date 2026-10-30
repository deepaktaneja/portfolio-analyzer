from performance.xirr import (
    calculate_portfolio_twr,
    calculate_portfolio_xirr,
    calculate_stock_xirr,
    get_xirr_cashflow_audit,
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
