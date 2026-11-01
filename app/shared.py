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


def load_transactions(db_path="data/xirr.sqlite3"):
    from src.pdf_parser import parse_folder
    from src.store import SQLiteStore
    store = SQLiteStore(db_path)
    txs = store.all_transactions()
    if not txs:
        folder = default_statement_folder()
        if os.path.isdir(folder):
            txs = parse_folder(folder, store=store)
    return txs


def load_holdings_df(folder=None):
    from src.holdings import load_holdings
    folder = folder or default_portfolio_folder()
    if not os.path.isdir(folder):
        return None
    try:
        return load_holdings(folder)
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


def price_lookup_factory(market_db="data/market_data.sqlite3", start_date=None, end_date=None):
    from market_data.prices import get_stock_price, get_stock_prices
    from market_data.repository import MarketDataStore

    cache = {}

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
        key = (key_sid, key_date)
        if key in cache:
            return cache[key]
        try:
            val = get_stock_price(key_sid, key_date, db_path=market_db)
        except Exception:  # noqa: BLE001
            val = None
        cache[key] = val
        return val

    return _lookup


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
