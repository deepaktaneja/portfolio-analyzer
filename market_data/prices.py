from __future__ import annotations

import os
import time
from datetime import date as _date

from market_data.repository import MarketDataStore

DEFAULT_MARKET_DB = "data/market_data.sqlite3"


def _store(db_path=None):
    if db_path is None:
        db_path = "data/market_data.sqlite3"
    return MarketDataStore(db_path)


def get_stock_price(security_id, date, db_path=None, exchange=None, allow_last=True):
    return _store(db_path).get_stock_price(security_id, date, exchange=exchange, allow_last=allow_last)


def get_stock_prices(security_id, start, end, db_path=None, exchange=None):
    return _store(db_path).get_stock_prices(security_id, start, end, exchange=exchange)


# ---------------------------------------------------------------------------
# On-demand price fetch (Yahoo Finance) for dates the local DB does not cover
# ---------------------------------------------------------------------------
#
# The local market-data DB holds a bounded history (it starts wherever the last
# bulk import stopped). Historical XIRR runs therefore need a way to resolve a
# price for an arbitrary past date. These helpers fetch *one security over one
# window* per call, cache the result in-process and (by default) persist it so
# later renders are served from the DB.
#
# Env switches:
#   ICICI_PRICE_FETCH_FALLBACK=false      disable all network fetches
#   ICICI_PRICE_FETCH_PERSIST=false       keep fetched rows in memory only
#   ICICI_PRICE_FETCH_MAX_SYMBOLS=<n>     per-lookup fetch budget (0 = unlimited)
#   ICICI_PRICE_PROVIDER=auto|yfinance|yahoo_chart
#                                         provider preference; auto = yfinance,
#                                         then the plain-HTTP Yahoo chart API

_FETCH_CACHE: dict[tuple[str, str, str, str], list[tuple[str, float]]] = {}
_FETCH_MISSES: set[tuple[str, str, str, str]] = set()


def _db_path(db_path=None) -> str:
    return str(db_path or DEFAULT_MARKET_DB)


def _iso(value):
    """Normalise a date-like value to ``YYYY-MM-DD`` (``None`` stays ``None``)."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value).strip()[:10]


def _env_flag(name: str, default: str = "true") -> bool:
    raw = os.environ.get(name, default)
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "")


def price_fetch_enabled() -> bool:
    """Whether missing prices may be fetched from the network on demand."""
    return _env_flag("ICICI_PRICE_FETCH_FALLBACK", "true")


def price_fetch_persist_enabled() -> bool:
    """Whether on-demand fetches are written back into the local market DB."""
    return _env_flag("ICICI_PRICE_FETCH_PERSIST", "true")


def price_fetch_symbol_budget() -> int:
    """Max distinct securities fetched per price-lookup instance (0 = unlimited)."""
    try:
        return max(0, int(os.environ.get("ICICI_PRICE_FETCH_MAX_SYMBOLS", "10")))
    except (TypeError, ValueError):
        return 10


def reset_price_fetch_cache() -> None:
    """Clear the in-process price cache and request spacing (used by tests)."""
    _FETCH_CACHE.clear()
    _FETCH_MISSES.clear()
    _LAST_FETCH_TS[0] = 0.0


def resolve_provider_symbol(security_id, db_path=None, provider=None):
    """Return the provider ticker (e.g. ``RELIANCE.NS``) stored for a security."""
    sid = str(security_id or "").strip().upper()
    if not sid:
        return None
    store = _store(db_path)
    rows = []
    if provider:
        try:
            rows = store.get_security_symbol_map(security_id=sid, provider=provider)
        except Exception:  # noqa: BLE001
            rows = []
    if not rows:
        try:
            rows = store.get_security_symbol_map(security_id=sid)
        except Exception:  # noqa: BLE001
            rows = []
    for r in rows or []:
        sym = str(r.get("provider_symbol") or r.get("symbol") or "").strip()
        if sym:
            return sym
    return None


def price_provider_name() -> str:
    """Configured provider preference (``auto`` = yfinance, then the HTTP chart API)."""
    return str(os.environ.get("ICICI_PRICE_PROVIDER", "auto")).strip().lower() or "auto"


def _provider_chain(provider=None):
    """Ordered providers to try for one fetch.

    ``provider`` may be a provider instance (tests/UI), a name string, or
    ``None`` to use ``ICICI_PRICE_PROVIDER``. ``auto`` tries the yfinance
    library first and falls back to the plain-HTTP Yahoo chart endpoint.
    """
    if provider is not None and hasattr(provider, "fetch_prices"):
        return [provider]
    name = str(provider).strip().lower() if isinstance(provider, str) else price_provider_name()
    try:
        from market_data.providers import YahooChartProvider, YFinanceProvider
    except Exception:  # noqa: BLE001
        return []
    if name in ("yfinance", "yahoo", "yf"):
        return [YFinanceProvider()]
    if name in ("yahoo_chart", "yahoo-chart", "yahoo_http", "chart", "http"):
        return [YahooChartProvider()]
    return [YFinanceProvider(), YahooChartProvider()]


def _clean_price_rows(rows, sid, start, end, exchange, source):
    """Normalise provider rows: positive closes, in range, with provider metadata."""
    cleaned = []
    for r in rows or []:
        close = r.get("close_price") if r.get("close_price") is not None else r.get("close")
        try:
            close = float(close)
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        trade_date = _iso(r.get("trade_date"))
        if not trade_date:
            continue
        if start and trade_date < start:
            continue
        if end and trade_date > end:
            continue
        rr = dict(r)
        rr["security_id"] = sid
        rr["trade_date"] = trade_date
        rr["close_price"] = close
        rr.setdefault("exchange", exchange)
        rr.setdefault("source", source)
        rr.setdefault("provider", source)
        rr.setdefault("verification_status", "unverified")
        cleaned.append(rr)
    return cleaned



def fetch_stock_prices(security_id, start=None, end=None, db_path=None, provider=None,
                       persist=None, exchange="NSE", symbol=None):
    """Fetch daily closes for one security and optionally store them.

    Returns a summary dict instead of raising, so UI and XIRR paths can report
    per-security progress: ``security_id``, ``symbol``, ``rows_fetched``,
    ``upserted``, ``source``, ``prices`` (sorted ``(date, close)`` pairs) and
    ``errors`` (list of short reason strings).
    """
    sid = str(security_id or "").strip().upper()
    out = {
        "security_id": sid,
        "symbol": None,
        "rows_fetched": 0,
        "upserted": 0,
        "source": None,
        "prices": [],
        "errors": [],
    }
    if not sid:
        out["errors"].append("missing_security_id")
        return out
    s, e = _iso(start), _iso(end)
    if s and e and s > e:
        out["errors"].append("start_after_end")
        return out
    sym = str(symbol).strip() if symbol else resolve_provider_symbol(sid, db_path=db_path)
    if not sym:
        out["errors"].append("no_symbol_mapping")
        return out
    out["symbol"] = sym
    cleaned: list[dict] = []
    source = None
    for prov in _provider_chain(provider):
        name = getattr(prov, "name", None) or "provider"
        try:
            rows = prov.fetch_prices(
                symbol=sym, start=s, end=e, security_id=sid, exchange=exchange
            ) or []
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"{name}: {exc}")
            continue
        cleaned = _clean_price_rows(rows, sid, s, e, exchange, name)
        if cleaned:
            source = name
            break
        out["errors"].append(f"{name}: no rows")

    out["source"] = source
    out["rows_fetched"] = len(cleaned)
    out["prices"] = sorted({(r["trade_date"], r["close_price"]) for r in cleaned})
    if cleaned and (price_fetch_persist_enabled() if persist is None else persist):
        try:
            out["upserted"] = _store(db_path).upsert_stock_prices(cleaned)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"persist_failed: {exc}")
    return out


def _fetch_window(day: str, start=None, end=None, today: str | None = None):
    """Coverage window that contains ``day`` and the caller's period bounds."""
    today = today or _iso(_date.today())
    lo = min(_iso(start) or day, day)
    hi = max(_iso(end) or day, day)
    hi = min(hi, today)
    if lo > hi:
        return None
    return lo, hi


def _fetch_window_rows(sid: str, window, db_path=None, persist=None):
    """Fetch and cache one security's prices for one window."""
    key = (_db_path(db_path), sid, window[0], window[1])
    if key in _FETCH_CACHE:
        return _FETCH_CACHE[key]
    if key in _FETCH_MISSES:
        return []
    res = fetch_stock_prices(sid, start=window[0], end=window[1], db_path=db_path, persist=persist)
    rows = list(res.get("prices") or [])
    if rows:
        _FETCH_CACHE[key] = rows
    else:
        _FETCH_MISSES.add(key)
    return rows


def _price_on_or_before(rows, day: str):
    """Last known close on or before ``day`` (mirrors ``allow_last=True``)."""
    for trade_date, price in reversed(rows or []):
        if trade_date <= day:
            return price
    return None


def get_stock_price_with_fallback(security_id, date, db_path=None, start=None, end=None,
                                  persist=None, allow_last=True):
    """Price on (or before) ``date``, falling back to an on-demand fetch.

    The DB is always consulted first, so normal operation stays offline. Only a
    genuine miss triggers a provider call, and only when
    :func:`price_fetch_enabled` is true.
    """
    sid = str(security_id or "").strip().upper()
    day = _iso(date)
    if not sid or not day:
        return None
    try:
        price = _store(db_path).get_stock_price(sid, day, allow_last=allow_last)
    except Exception:  # noqa: BLE001
        price = None
    if price is not None:
        return price
    if not price_fetch_enabled():
        return None
    if day > _iso(_date.today()):
        return None
    window = _fetch_window(day, start=start, end=end)
    if window is None:
        return None
    rows = _fetch_window_rows(sid, window, db_path=db_path, persist=persist)
    return _price_on_or_before(rows, day)


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


def sync_benchmark_history(benchmark, start, end, db_path=None, source_url=None, path=None, index_name=None,
                           use_archive=False, archive_max_requests=250):
    from market_data.fetcher import (
        sync_index_tri,
        sync_index_tri_nse_archive,
        sync_index_tri_yfinance,
    )
    try:
        return sync_index_tri(benchmark, start=start, end=end, db_path=db_path, source_url=source_url, path=path, index_name=index_name)
    except Exception as official_err:  # noqa: BLE001
        if path or source_url:
            raise
        if use_archive:
            try:
                res = sync_index_tri_nse_archive(
                    benchmark, start, end, db_path=db_path, max_requests=archive_max_requests,
                )
                res["fallback_reason"] = str(official_err)
                if res.get("upserted"):
                    return res
            except Exception:  # noqa: BLE001
                pass
        res = sync_index_tri_yfinance(benchmark, start=start, end=end, db_path=db_path)
        res["fallback_reason"] = str(official_err)
        return res
