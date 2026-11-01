import time

from performance.benchmarks import calculate_annual_returns


def _txs():
    out = []
    for i in range(2000):
        out.append({
            "trade_date": f"{2014 + (i % 10)}-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
            "isin": f"INE{i % 50}",
            "buy_sell": "BUY" if i % 3 else "SELL",
            "quantity": 10,
            "net_amount": 1000.0,
        })
    return out


def test_phase3_annual_performance_under_10s():
    txs = _txs()
    def pl(sid, d):
        return 100.0
    def idx(d):
        return 1000.0
    t0 = time.perf_counter()
    rows = calculate_annual_returns(
        txs, price_lookup=pl, index_lookup_50=idx, index_lookup_500=idx,
        start_date="2014-01-01", end_date="2023-12-31", frequency="calendar",
    )
    elapsed = time.perf_counter() - t0
    assert rows is not None
    assert elapsed < 10.0, f"annual performance too slow: {elapsed:.2f}s"