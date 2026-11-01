from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import Any

from market_data.providers import ManualCsvProvider, YFinanceProvider
from market_data.repository import MarketDataStore


def _now() -> str:
    from datetime import UTC
    return datetime.now(UTC).isoformat()


def _default_db(db_path=None):
    return db_path or "data/market_data.sqlite3"


def _resolve_provider(provider=None, **kwargs):
    if provider is None:
        provider = kwargs.get("provider_name") or kwargs.get("provider_type") or "manual"
    if isinstance(provider, str):
        name = provider.strip().lower()
        if name in ("manual", "manual_csv", "csv"):
            return ManualCsvProvider(**{k: v for k, v in kwargs.items() if k in ("path", "csv_path", "file_path", "directory", "source")})
        if name in ("yfinance", "yahoo", "yf"):
            return YFinanceProvider(**{k: v for k, v in kwargs.items() if k in ("timeout", "retries", "auto_adjust", "actions")})
        if os.path.exists(name):
            return ManualCsvProvider(path=name)
        return ManualCsvProvider()
    return provider


def _discover_security_ids(db_path=None, tx_db_path=None):
    ids = []
    try:
        store = MarketDataStore(_default_db(db_path))
        for r in store.get_security_symbol_map():
            ids.append(r.get("security_id"))
        for sid in store.get_market_data_quality_report().get("securities", []):
            ids.append(sid)
    except Exception:
        pass
    try:
        tdb = tx_db_path or os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3")
        if os.path.exists(tdb):
            from src.store import SQLiteStore
            st = SQLiteStore(tdb)
            for t in st.all_transactions():
                sid = t.get("isin") or t.get("security")
                if sid:
                    ids.append(str(sid).strip().upper())
    except Exception:
        pass
    out = []
    seen = set()
    for s in ids:
        if not s:
            continue
        s = str(s).strip().upper()
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _symbol_for(store, security_id, symbol_map=None):
    if symbol_map and security_id in symbol_map:
        return symbol_map[security_id]
    try:
        rows = store.get_security_symbol_map(security_id=security_id)
        if rows:
            r = rows[0]
            return r.get("provider_symbol") or r.get("symbol") or security_id
    except Exception:
        pass
    return security_id


def sync_prices(security_id, provider=None, start=None, end=None, db_path=None, exchange="NSE", symbol_map=None, max_retries=3, throttle_seconds=0.0, **kwargs):
    prov = _resolve_provider(provider, **kwargs)
    store = MarketDataStore(_default_db(db_path))
    sid = str(security_id).strip().upper()
    symbol = _symbol_for(store, sid, symbol_map)
    errors = []
    rows = []
    for attempt in range(max_retries):
        try:
            rows = prov.fetch_prices(symbol=symbol, start=start, end=end, security_id=sid, exchange=exchange)
            break
        except Exception as e:
            errors.append(str(e))
            time.sleep(0.1)
    else:
        errors.append("max retries exceeded")

    for r in rows:
        r.setdefault("provider", getattr(prov, "name", "unknown"))
    n = store.upsert_stock_prices(rows) if rows else 0

    actions_n = 0
    try:
        acts = prov.fetch_corporate_actions(symbol=symbol, start=start, end=end, security_id=sid)
        if acts:
            actions_n = store.upsert_corporate_actions(acts)
    except Exception as e:
        errors.append(str(e))

    divs_n = 0
    try:
        divs = prov.fetch_dividends(symbol=symbol, start=start, end=end, security_id=sid)
        if divs:
            divs_n = store.upsert_dividend_events(divs)
    except Exception as e:
        errors.append(str(e))

    if throttle_seconds:
        time.sleep(throttle_seconds)

    return {"security_id": sid, "prices_upserted": n, "actions_upserted": actions_n, "dividends_upserted": divs_n, "errors": errors}


def sync_security_prices(*args, **kwargs):
    return sync_prices(*args, **kwargs)
def sync_all_market_data(db_path=None, provider=None, start=None, end=None, security_ids=None, symbols=None, exchange="NSE", throttle_seconds=0.0, max_retries=3, tx_db_path=None, symbol_map=None, include_actions=True, include_dividends=True, benchmark_index_ids=None, **kwargs):
    store = MarketDataStore(_default_db(db_path))
    prov = _resolve_provider(provider, **kwargs)
    if security_ids is None:
        security_ids = _discover_security_ids(db_path, tx_db_path)
    if symbols and isinstance(symbols, dict):
        symbol_map = {**(symbol_map or {}), **symbols}
    if start is None:
        start = "2000-01-01"
    if end is None:
        end = date.today().isoformat()

    run_id = store.start_sync_run(provider=getattr(prov, "name", str(prov)), scope="all", security_count=len(security_ids))

    prices_total = 0
    actions_total = 0
    divs_total = 0
    errors_all = []
    for sid in security_ids:
        try:
            res = sync_prices(security_id=sid, provider=prov, start=start, end=end, db_path=db_path, exchange=exchange, symbol_map=symbol_map, max_retries=max_retries, throttle_seconds=throttle_seconds)
            prices_total += res.get("prices_upserted", 0)
            actions_total += res.get("actions_upserted", 0)
            divs_total += res.get("dividends_upserted", 0)
            errors_all.extend(res.get("errors", []))
        except Exception as e:
            errors_all.append(f"{sid}: {e}")

    bench_total = 0
    if benchmark_index_ids:
        for iid in benchmark_index_ids:
            try:
                rows = prov.fetch_benchmark(symbol=iid, start=start, end=end, security_id=iid)
                for r in rows:
                    r.setdefault("provider", getattr(prov, "name", "unknown"))
                bench_total += store.upsert_benchmark_history(rows)
            except Exception as e:
                errors_all.append(f"benchmark {iid}: {e}")

    if symbol_map:
        try:
            rows = [{"security_id": k, "symbol": v, "provider_symbol": v, "provider": getattr(prov, "name", "manual")} for k, v in symbol_map.items()]
            store.upsert_security_symbol_map(rows)
        except Exception as e:
            errors_all.append(str(e))

    status = "success" if not errors_all else "partial"
    store.finish_sync_run(run_id, status=status, rows_inserted=prices_total, errors=len(errors_all), error_text="; ".join(errors_all)[:500], details={"actions": actions_total, "dividends": divs_total, "benchmarks": bench_total})
    return {
        "provider": getattr(prov, "name", str(prov)),
        "securities": security_ids,
        "prices_upserted": prices_total,
        "actions_upserted": actions_total,
        "dividends_upserted": divs_total,
        "benchmarks_upserted": bench_total,
        "errors": errors_all,
        "sync_run_id": run_id,
    }


def sync_missing_prices(start, end, db_path=None, provider=None, security_ids=None, exchange="NSE", tx_db_path=None, **kwargs):
    store = MarketDataStore(_default_db(db_path))
    if security_ids is None:
        missing = store.get_missing_prices(start, end)
        security_ids = sorted({m[0] for m in missing})
    if not security_ids:
        security_ids = _discover_security_ids(db_path, tx_db_path)
    return sync_all_market_data(db_path=db_path, provider=provider, start=start, end=end, security_ids=security_ids, exchange=exchange, tx_db_path=tx_db_path, **kwargs)


def sync_market_data(*args, **kwargs):
    return sync_all_market_data(*args, **kwargs)


def discover_securities(db_path=None, tx_db_path=None):

    return _discover_security_ids(db_path, tx_db_path)


def sync_all(*args, **kwargs):
    return sync_all_market_data(*args, **kwargs)


def fetch_all_market_data(*args, **kwargs):
    return sync_all_market_data(*args, **kwargs)
def sync_index_tri(index_id, start=None, end=None, db_path=None, source_url=None, path=None, index_name=None, **kwargs):
    import os
    import tempfile
    from market_data.adapters import import_index_tri
    from market_data.repository import MarketDataStore

    iid = str(index_id).strip().upper()
    if index_name is None:
        index_name = "NIFTY 50" if "50" in iid and "500" not in iid else "NIFTY 500"
    if start is None:
        start = "01-Jan-2000"
    if end is None:
        end = datetime.now().strftime("%d-%b-%Y")
    # normalize dates to DD-MMM-YYYY if ISO provided
    def _norm(d):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                return datetime.strptime(str(d).strip(), fmt).strftime("%d-%b-%Y")
            except ValueError:
                continue
        return str(d).strip()
    start = _norm(start)
    end = _norm(end)

    tmp_path = None
    try:
        if path:
            tmp_path = path
        elif source_url:
            import requests
            r = requests.get(source_url, timeout=30)
            r.raise_for_status()
            suffix = ".csv" if "csv" in (source_url.lower()) else ".html"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(r.content)
        else:
            import requests
            session = requests.Session()
            try:
                session.get("https://www.niftyindices.com/reports/historical-data", timeout=10)
            except Exception:
                pass
            payload = {"cinfo": "{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}" % (index_name, start, end, index_name)}
            headers = {
                "Content-Type": "application/json; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.niftyindices.com/reports/historical-data",
                "User-Agent": "Mozilla/5.0",
            }
            r = session.post("https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString", json=payload, headers=headers, timeout=60)
            r.raise_for_status()
            data = r.json()
            content = data.get("d", data)
            if isinstance(content, (dict, list)):
                import json
                content = json.dumps(content)
            content = str(content)
            suffix = ".html" if "<table" in content.lower() else ".csv"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
        parsed, warnings = import_index_tri(tmp_path, iid, source="niftyindices")
        store = MarketDataStore(_default_db(db_path))
        n = store.upsert_index_tri(parsed)
        return {"index_id": iid, "rows": len(parsed), "upserted": n, "warnings": warnings}
    finally:
        if tmp_path and tmp_path != path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def sync_dividend_events(path, db_path=None, source=None):
    from market_data.adapters import import_dividend_events
    from market_data.repository import MarketDataStore
    parsed, warnings = import_dividend_events(path, source=source)
    store = MarketDataStore(_default_db(db_path))
    n = store.upsert_dividend_events(parsed)
    return {"rows": len(parsed), "upserted": n, "warnings": warnings}
