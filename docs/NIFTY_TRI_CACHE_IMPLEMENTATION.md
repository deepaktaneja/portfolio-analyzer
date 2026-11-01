# Nifty TRI Cache Implementation

## Provider used
- Optional Niftyindices public endpoint via `sync_index_tri`.
- Supports local CSV/HTML import.
- YFinance is not used for TRI (price index only).

## Limitation
- The Niftyindices endpoint is an undocumented public endpoint, not an officially authorized API.
- For strictly authorized data, import official NSE/Niftyindices files.

## Database schema
- Table: `index_tri`
- PK: `(index_id, trade_date)`
- Fields: level, source, imported_at, provider, verification_status, raw_value.

## Behavior
- `get_index_tri` and `get_benchmark_history` read only from SQLite.
- No network calls during Benchmark Analysis or Annual Performance.
- `sync_benchmark_history` performs explicit sync.
- Missing dates detected using stock price trading dates or weekday fallback.

## Tests
- `tests/unit/test_benchmark_cache.py`: 2 passed.

## Usage
- Use Market Data tab to sync/import Nifty 50/500 TRI.
- Benchmark Analysis and Annual Performance use cached SQLite data.