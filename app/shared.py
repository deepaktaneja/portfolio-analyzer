"""Shared helpers for the Streamlit dashboard (Stage 09)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def default_statement_folder() -> str:
    return str((DATA_DIR / "statements").resolve())


def default_portfolio_folder() -> str:
    return str((DATA_DIR / "portfolio").resolve())


def load_transactions(db_path="data/xirr.sqlite3", person_id=None):
    from src.people import PersonStore
    store = PersonStore(db_path)
    if person_id is None:
        return store.assigned_transactions()
    return store.all_transactions(person_id)


def resolve_person_name(db_path="data/xirr.sqlite3", person_id=None, person_name=None):
    """Resolve the display name for the active person filter.

    Prefers an explicit ``person_name``; otherwise looks the name up from the
    people store using ``person_id``. Returns ``None`` for the "All people"
    scope so callers aggregate across every person. Resolving by id guarantees
    a specific person is never silently collapsed into the aggregate
    (root ``data/mf``) scope, which would leak another person's MF data.
    """
    if person_name:
        return str(person_name)
    if person_id is None:
        return None
    try:
        from src.people import PersonStore
        store = PersonStore(str(db_path).replace("sqlite:///", "", 1))
        for p in store.list_people():
            if int(p["id"]) == int(person_id):
                return str(p["name"])
    except Exception:  # noqa: BLE001
        return None
    return None


def _holdings_rows_to_df(rows):
    import pandas as pd

    from market_data.sector import enrich_holdings_with_sectors
    df = pd.DataFrame(rows)
    for c in ("quantity", "avg_price", "current_price", "cost_value", "market_value"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    mv = df["market_value"] if "market_value" in df.columns else pd.Series([None] * len(df))
    calc = df["quantity"].fillna(0) * df["current_price"].fillna(0)
    df["current_value"] = mv.fillna(calc)
    df["sector"] = None
    df["market_cap"] = None
    enrich_holdings_with_sectors(df)
    return df


def load_holdings_df(folder=None, person_id=None):
    if person_id is not None:
        try:
            from src.people import PersonStore
            db = os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3").replace("sqlite:///", "", 1)
            rows = PersonStore(db).get_latest_holdings(person_id)
            if rows:
                return _holdings_rows_to_df(rows)
        except Exception:  # noqa: BLE001
            return None
        return None
    if folder is None:
        try:
            from src.people import PersonStore
            db = os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3").replace("sqlite:///", "", 1)
            rows = PersonStore(db).get_aggregate_holdings()
            if rows:
                return _holdings_rows_to_df(rows)
        except Exception:  # noqa: BLE001
            return None
        return None
    from src.holdings import load_holdings
    if not os.path.isdir(folder):
        return None
    try:
        df = load_holdings(folder)
        from market_data.sector import enrich_holdings_with_sectors
        enrich_holdings_with_sectors(df)
        return df
    except Exception:  # noqa: BLE001
        return None

def inr(value):
    if value is None:
        return "N/A"
    try:
        return f"Rs {float(value):,.1f}"
    except (TypeError, ValueError):
        return str(value)


def _norm_date(v):
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _holdings_as_of_date(holdings):
    """Best-effort as-of date for an imported holdings snapshot."""
    attrs = getattr(holdings, "attrs", None)
    if not hasattr(attrs, "get"):
        return None
    for key in ("as_of_date", "as_of", "date"):
        value = attrs.get(key)
        if not value:
            continue
        norm = _norm_date(value)
        if norm:
            return norm[:10]
    return None


def _snapshot_price_usable(end_key, snapshot_key=None, max_gap_days=45):
    """Whether a holdings-snapshot price may stand in for the end-date close.

    Snapshot prices are current-state values. They are only meaningful for an end
    date close to the snapshot (a 2026 statement price must never be used as the
    terminal price for a 2015 period). When the snapshot's as-of date is unknown
    "now" is assumed.
    """
    from datetime import date as _date

    def _parse(value):
        try:
            return _date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            return None

    end_d = _parse(end_key)
    if end_d is None:
        return False
    ref_d = _parse(snapshot_key) or _date.today()
    return abs((ref_d - end_d).days) <= max_gap_days


def price_lookup_factory(market_db="data/market_data.sqlite3", start_date=None, end_date=None,
                         holdings=None, fetch_missing=None, persist_fetched=None,
                         max_fetch_symbols=None):
    """Build a ``price_lookup(security_id, date)`` callable for XIRR/TWR.

    Resolution order:
      1. the local market-data DB (offline and fast — normal operation);
      2. the imported holdings snapshot, but only for the end date and only when
         the snapshot is close to it (see :func:`_snapshot_price_usable`);
      3. an on-demand Yahoo Finance fetch for the requested window, cached
         in-process and persisted to the DB by default.

    ``fetch_missing`` / ``persist_fetched`` / ``max_fetch_symbols`` override the
    ``ICICI_PRICE_FETCH_*`` env switches (a per-lookup security budget keeps a
    page render bounded; use Data & Settings to pre-fetch a whole period).
    """
    from market_data.prices import (
        get_stock_price,
        get_stock_price_with_fallback,
        get_stock_prices,
        price_fetch_enabled,
        price_fetch_persist_enabled,
        price_fetch_symbol_budget,
    )
    from market_data.repository import MarketDataStore

    cache = {}
    fallback = {}
    if holdings is not None:
        try:
            for _, r in holdings.iterrows():
                isin = str(r.get("isin") or "").strip().upper()
                if not isin:
                    continue
                price = r.get("current_price")
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    price = None
                if price is None or price <= 0:
                    qty = r.get("quantity")
                    value = r.get("current_value")
                    try:
                        qty = float(qty)
                        value = float(value)
                    except (TypeError, ValueError):
                        qty = None
                    if qty:
                        price = value / qty
                if price is not None and price > 0:
                    fallback[isin] = price
        except Exception:  # noqa: BLE001
            fallback = {}
    end_key = _norm_date(end_date) if end_date is not None else None
    start_key = _norm_date(start_date) if start_date is not None else None
    snapshot_key = _holdings_as_of_date(holdings)
    snapshot_ok = bool(fallback) and _snapshot_price_usable(end_key, snapshot_key)

    if fetch_missing is None:
        fetch_missing = price_fetch_enabled()
    if persist_fetched is None:
        persist_fetched = price_fetch_persist_enabled()
    budget = (
        price_fetch_symbol_budget()
        if max_fetch_symbols is None
        else max(0, int(max_fetch_symbols))
    )
    fetched: set[str] = set()
    skipped: set[str] = set()

    if start_date is not None and end_date is not None:
        sd = _norm_date(start_date)
        ed = _norm_date(end_date)
        try:
            md = MarketDataStore(market_db)
            with md._conn() as c:
                secs = [r[0] for r in c.execute(
                    "SELECT DISTINCT security_id FROM stock_prices"
                ).fetchall()]
            for sid in secs:
                try:
                    for d, p in get_stock_prices(sid, sd, ed, db_path=market_db):
                        cache[(str(sid).strip().upper(), str(d))] = p
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass

    def _lookup(sid, d):
        key_sid = str(sid).strip().upper()
        key_date = _norm_date(d)
        if not key_sid or not key_date:
            return None
        key = (key_sid, key_date)
        if key in cache:
            return cache[key]
        try:
            val = get_stock_price(key_sid, key_date, db_path=market_db)
        except Exception:  # noqa: BLE001
            val = None
        if (
            val is None
            and snapshot_ok
            and end_key is not None
            and key_date[:10] == end_key[:10]
        ):
            val = fallback.get(key_sid)
        if (
            val is None
            and fetch_missing
            and (start_key or end_key)
            and key_sid not in fetched
        ):
            if budget and len(fetched) >= budget:
                skipped.add(key_sid)
            else:
                fetched.add(key_sid)
                try:
                    val = get_stock_price_with_fallback(
                        key_sid,
                        key_date[:10],
                        db_path=market_db,
                        start=start_key,
                        end=end_key,
                        persist=persist_fetched,
                    )
                except Exception:  # noqa: BLE001
                    val = None
        cache[key] = val
        return val

    _lookup.fetched_securities = fetched  # type: ignore[attr-defined]
    _lookup.skipped_securities = skipped  # type: ignore[attr-defined]
    return _lookup


def compute_dividend_cashflows(txs, dividend_events, tax_rate: float = 0.30):
    """Convert per-share dividend events into total cashflows.

    The market-data store keeps ``dividend_per_share`` per ex-date. XIRR needs a
    total rupee amount, so this multiplies by the quantity actually held on the
    ex-date, reconstructed from the (person-filtered) transactions. ``gross`` is
    the pre-tax amount; ``net`` applies the assumed ``tax_rate`` (default 30%).
    """
    from collections import defaultdict

    flows: dict[str, list] = defaultdict(list)
    for t in txs or []:
        isin = str(t.get("isin") or "").strip().upper()
        d = str(t.get("trade_date") or t.get("settlement_date") or "")[:10]
        if not isin or not d:
            continue
        try:
            q = float(t.get("quantity") or 0.0)
        except (TypeError, ValueError):
            q = 0.0
        side = str(t.get("buy_sell") or "").strip().upper()
        if side == "BUY":
            flows[isin].append((d, q))
        elif side == "SELL":
            flows[isin].append((d, -q))
    for v in flows.values():
        v.sort(key=lambda x: x[0])

    events: dict[str, list] = defaultdict(list)
    for e in dividend_events or []:
        if not isinstance(e, dict):
            continue
        isin = str(e.get("security_id") or e.get("isin") or "").strip().upper()
        if not isin or isin not in flows:
            continue
        ex = str(e.get("ex_date") or e.get("payment_date") or "")[:10]
        try:
            per = float(e.get("dividend_per_share") or e.get("gross") or e.get("amount") or 0.0)
        except (TypeError, ValueError):
            per = 0.0
        if not ex or per <= 0:
            continue
        events[isin].append((ex, per))

    out = []
    for isin, evs in events.items():
        evs.sort(key=lambda x: x[0])
        tx_list = flows[isin]
        idx = 0
        qty = 0.0
        for ex, per in evs:
            while idx < len(tx_list) and tx_list[idx][0] <= ex:
                qty += tx_list[idx][1]
                idx += 1
            if qty > 0:
                gross = per * qty
                tax = gross * float(tax_rate or 0.0)
                out.append({
                    "security_id": isin,
                    "payment_date": ex,
                    "gross": gross,
                    "tax": round(tax, 2),
                    "net": round(gross - tax, 2),
                    "status": "confirmed",
                })
    out.sort(key=lambda r: r["payment_date"])
    return out


def tx_hash(txs):
    try:
        return hashlib.sha256(
            json.dumps(txs, sort_keys=True, default=str).encode()
        ).hexdigest()
    except Exception:  # noqa: BLE001
        return str(hash(tuple(str(t) for t in (txs or []))))

def format_df_one_decimal(df):
    import pandas as pd
    if df is None:
        return df
    try:
        if df.empty:
            return df
    except Exception:
        return df
    out = df.copy()
    for col in out.columns:
        try:
            if pd.api.types.is_numeric_dtype(out[col]):
                out[col] = out[col].map(lambda x: (f"{float(x):.1f}" if pd.notna(x) else ""))
        except Exception:
            pass
    return out
def fmt1(value):
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return str(value)

def fmt_pct(value):
    if value is None:
        return "N/A"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)

def security_name(isin, txs=None, holdings=None):
    sid = str(isin or "").strip().upper()
    if not sid:
        return ""
    if holdings is not None:
        try:
            if not holdings.empty and "isin" in holdings.columns:
                m = holdings[holdings["isin"].astype(str).str.strip().str.upper() == sid]
                if not m.empty:
                    for col in ("symbol", "security", "company", "name"):
                        if col in m.columns:
                            val = m.iloc[0].get(col)
                            if val is not None and str(val).strip() and str(val).strip().lower() not in ("nan", "none", "nat"):
                                return str(val).strip()
        except Exception:
            pass


def security_name(isin, txs=None, holdings=None):
    sid = str(isin or "").strip().upper()
    if not sid:
        return ""
    if holdings is not None:
        try:
            m = holdings[holdings["isin"].astype(str).str.strip().str.upper() == sid]
            if not m.empty:
                for col in ("symbol", "security"):
                    if col in m.columns:
                        v = m.iloc[0].get(col)
                        if v is not None:
                            return str(v).strip()
        except Exception:
            pass
    for t in (txs or []):
        if str(t.get("isin") or "").strip().upper() == sid:
            for key in ("security", "symbol"):
                v = t.get(key)
                if v is not None:
                    return str(v).strip()
    return sid
