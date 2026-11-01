import time

from performance.benchmarks import calculate_annual_returns


def _txs(n=2000):
    out = []
    for i in range(n):
        out.append({
            "trade_date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
            "isin": f"INE{i % 50}",
            "buy_sell": "BUY" if i % 3 else "SELL",
            "quantity": 10,
            "net_amount": 1000.0,
        })
    return out


def test_annual_performance_timing_threshold():
    txs = _txs(1000)
    def pl(sid, d):
        return 100.0
    def idx(d):
        return 100.0
    t0 = time.perf_counter()
    rows = calculate_annual_returns(
        txs, price_lookup=pl, index_lookup_50=idx, index_lookup_500=idx,
        start_date="2024-01-01", end_date="2024-12-31", frequency="calendar",
    )
    elapsed = time.perf_counter() - t0
    assert rows is not None
    assert elapsed < 5.0, f"annual performance too slow: {elapsed:.2f}s"