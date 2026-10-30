from performance.benchmarks import (
    calculate_annual_returns,
    calculate_benchmark_xirr,
    compare_portfolio_benchmarks,
    simulate_benchmark_investment,
)


def idx(d):
    # simple index level: 100 + day index
    return 100.0 + (d.toordinal() - 738000)

def test_simulate():
    cfs = [("2024-01-01", -1000.0), ("2024-06-01", 200.0)]
    sim = simulate_benchmark_investment(cfs, idx, "2024-01-01", "2024-06-01")
    assert sim["units"] > 0
    assert sim["terminal_value"] is not None

def test_benchmark_xirr():
    cfs = [("2024-01-01", -1000.0)]
    res = calculate_benchmark_xirr(cfs, idx, "2024-01-01", "2024-06-01")
    assert res["xirr"] is not None

def test_annual_calendar():
    txs = [
        {"trade_date": "2024-01-15", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0},
        {"trade_date": "2024-06-15", "isin": "INE1", "buy_sell": "SELL", "quantity": 5, "net_amount": 600.0},
    ]
    def pl(sid, d): return 100.0
    rows = calculate_annual_returns(txs, price_lookup=pl, index_lookup_50=idx, index_lookup_500=idx, start_date="2024-01-01", end_date="2024-12-31", frequency="calendar")
    assert len(rows) == 1
    assert rows[0]["period"] == "2024"
    assert rows[0]["partial"] is False

def test_annual_financial():
    txs = [{"trade_date": "2024-04-15", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    def pl(sid, d): return 100.0
    rows = calculate_annual_returns(txs, price_lookup=pl, index_lookup_50=idx, index_lookup_500=idx, start_date="2024-04-15", end_date="2024-05-15", frequency="financial")
    assert len(rows) >= 1
    assert rows[0]["period"].startswith("FY")

def test_ytd():
    txs = [{"trade_date": "2024-03-15", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    def pl(sid, d): return 100.0
    rows = calculate_annual_returns(txs, price_lookup=pl, index_lookup_50=idx, index_lookup_500=idx, start_date="2024-03-15", end_date="2024-05-15", frequency="ytd")
    assert rows[0]["period"] == "YTD"
    assert rows[0]["partial"] is True

def test_compare():
    txs = [{"trade_date": "2024-01-15", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    def pl(sid, d): return 100.0
    res = compare_portfolio_benchmarks(txs, price_lookup=pl, index_lookup_50=idx, index_lookup_500=idx, start_date="2024-01-15", end_date="2024-06-15")
    assert "methodology_time_weighted" in res
    assert "methodology_cash_flow_matched" in res
