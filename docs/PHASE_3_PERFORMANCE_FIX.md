# Phase 3 Performance Fix

## Confirmed root cause
Annual Performance took ~33.6s for 5 periods on 3,075 transactions. Profiling showed:
- `calculate_portfolio_twr` and `calculate_portfolio_xirr` each reconstructed holdings separately.
- `_holdings_qty` performed a full transaction scan per security.
- Benchmark TRI lookups hit SQLite repeatedly.
- No network calls or infinite loops were involved.

## Files modified
- `performance/xirr.py`
  - Added `_holdings_as_of` to compute holdings in one pass.
  - Rewrote TWR valuation to precompute holdings per date in one pass.
  - Updated `_holdings_qty` and `calculate_portfolio_xirr` to reuse single-pass holdings.
- `performance/benchmarks.py`
  - Cached Nifty 50/500 TRI lookups in memory.
- `tests/regression/test_phase3_performance.py`
  - Added representative performance regression test.

## Performance
- Before: ~33.6s
- After: ~1.7s for the same dataset
- Target: under 10s (achieved)

## Financial accuracy
- Methodology unchanged.
- Existing XIRR/TWR/benchmark tests pass.

## Tests
- `tests/unit/test_performance_xirr.py`: passed
- `tests/unit/test_benchmarks.py`: passed
- `tests/regression/test_phase3_performance.py`: passed

## Remaining limitations
- Larger datasets or 10+ years of daily prices may still need further caching.
- XIRR solver depends on pyxirr availability.