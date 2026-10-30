from accounting.fifo import (
    calculate_realized_gains,
    calculate_unrealized_gains,
    get_fifo_lots,
    get_stock_profit_summary,
)

TX = [
    {
        "trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0,
    },
    {
        "trade_date": "2024-02-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 5, "net_amount": 600.0,
    },
    {
        "trade_date": "2024-03-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 7, "net_amount": 900.0,
    },
]

def test_lots():
    lots = get_fifo_lots(TX, as_of_date="2024-02-15")
    assert sum(l["qty"] for l in lots) == 15

def test_realized_gain():
    gains = calculate_realized_gains(TX, start_date="2024-03-01", end_date="2024-03-01")
    assert len(gains) == 1
    assert gains[0]["gain"] == 900.0 - 700.0

def test_unrealized():
    def price(sid, d): return 120.0
    res = calculate_unrealized_gains(TX, as_of_date="2024-03-15", price_lookup=price)
    assert res[0]["quantity"] == 8
    assert res[0]["unrealized_gain"] is not None

def test_profit_summary():
    def price(sid, d): return 120.0
    s = get_stock_profit_summary(TX, security_id="INE1", as_of_date="2024-03-15", price_lookup=price)
    assert s["remaining_qty"] == 8
    assert s["realized_gain"] == 900.0 - 700.0
    assert s["dividends"] == 0.0

def test_full_exit_retains_gain():
    tx = TX + [{"trade_date": "2024-04-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 8, "net_amount": 1000.0}]
    gains = calculate_realized_gains(tx, start_date="2024-01-01", end_date="2024-12-31")
    assert len(gains) == 2

