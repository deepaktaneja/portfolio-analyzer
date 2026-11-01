from __future__ import annotations

from market_data.repository import MarketDataStore


def _store(db_path=None):
    if db_path is None:
        db_path = "data/market_data.sqlite3"
    return MarketDataStore(db_path)


def get_stock_price(security_id, date, db_path=None, exchange=None, allow_last=True):
    return _store(db_path).get_stock_price(security_id, date, exchange=exchange, allow_last=allow_last)


def get_stock_prices(security_id, start, end, db_path=None, exchange=None):
    return _store(db_path).get_stock_prices(security_id, start, end, exchange=exchange)


def get_index_tri(index_id, date, db_path=None, allow_last=True):
    return _store(db_path).get_index_tri(index_id, date, allow_last=allow_last)


def get_missing_prices(start, end, db_path=None, security_ids=None):
    return _store(db_path).get_missing_prices(start, end, security_ids=security_ids)


def get_market_data_quality_report(db_path=None):
    return _store(db_path).get_market_data_quality_report()


def get_security_symbol_map(security_id=None, provider=None, db_path=None):
    return _store(db_path).get_security_symbol_map(security_id=security_id, provider=provider)


def get_symbol_map(security_id=None, provider=None, db_path=None):
    return _store(db_path).get_symbol_map(security_id=security_id, provider=provider)


def get_corporate_actions(security_id=None, verified_only=False, db_path=None):
    return _store(db_path).get_corporate_actions(security_id=security_id, verified_only=verified_only)


def get_dividend_events(security_id=None, verified_only=False, db_path=None):
    return _store(db_path).get_dividend_events(security_id=security_id, verified_only=verified_only)


def get_benchmark_history(index_id=None, start=None, end=None, db_path=None):
    return _store(db_path).get_benchmark_history(index_id=index_id, start=start, end=end)


def get_sync_runs(limit=100, db_path=None):
    return _store(db_path).get_sync_runs(limit=limit)


def sync_all_market_data(*args, **kwargs):
    from market_data.fetcher import sync_all_market_data as _f
    return _f(*args, **kwargs)


def sync_missing_prices(*args, **kwargs):
    from market_data.fetcher import sync_missing_prices as _f
    return _f(*args, **kwargs)


def sync_prices(*args, **kwargs):
    from market_data.fetcher import sync_prices as _f
    return _f(*args, **kwargs)


def discover_securities(*args, **kwargs):
    from market_data.fetcher import discover_securities as _f
    return _f(*args, **kwargs)


def get_dividends(security_id=None, verified_only=False, db_path=None):
    return get_dividend_events(security_id=security_id, verified_only=verified_only, db_path=db_path)


def get_corporate_action(security_id=None, verified_only=False, db_path=None):
    return get_corporate_actions(security_id=security_id, verified_only=verified_only, db_path=db_path)


def get_symbol_mapping(security_id=None, provider=None, db_path=None):
    return get_symbol_map(security_id=security_id, provider=provider, db_path=db_path)


def get_sync_run(run_id=None, db_path=None):
    return _store(db_path).get_sync_run(run_id=run_id)


def get_missing_benchmark_dates(benchmark, start, end, db_path=None):
    from datetime import date as _d, timedelta as _td
    store = _store(db_path)
    iid = str(benchmark).strip().upper()
    try:
        s = _d.fromisoformat(str(start))
        e = _d.fromisoformat(str(end))
    except ValueError:
        return []
    with store._conn() as c:
        stored = {r[0] for r in c.execute(
            "SELECT trade_date FROM index_tri WHERE index_id=:iid AND trade_date>=:s AND trade_date<=:e",
            {"iid": iid, "s": str(start), "e": str(end)},
        ).fetchall()}
        ref_rows = c.execute(
            "SELECT DISTINCT trade_date FROM stock_prices WHERE trade_date>=:s AND trade_date<=:e",
            {"s": str(start), "e": str(end)},
        ).fetchall()
    ref = {r[0] for r in ref_rows}
    if not ref:
        d = s
        while d <= e:
            if d.weekday() < 5:
                ref.add(d.isoformat())
            d = d + _td(days=1)
    return sorted(ref - stored)


def get_benchmark_sync_status(benchmark, db_path=None):
    store = _store(db_path)
    iid = str(benchmark).strip().upper()
    with store._conn() as c:
        row = c.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date), MAX(imported_at) FROM index_tri WHERE index_id=:iid", {"iid": iid}).fetchone()
        src = [r[0] for r in c.execute("SELECT DISTINCT COALESCE(source, provider) FROM index_tri WHERE index_id=:iid", {"iid": iid}).fetchall()]
    return {"benchmark": iid, "records": row[0] or 0, "earliest": row[1], "latest": row[2], "last_imported": row[3], "sources": src}


def sync_benchmark_history(benchmark, start, end, db_path=None, source_url=None, path=None, index_name=None):
    from market_data.fetcher import sync_index_tri
    return sync_index_tri(benchmark, start=start, end=end, db_path=db_path, source_url=source_url, path=path, index_name=index_name)
