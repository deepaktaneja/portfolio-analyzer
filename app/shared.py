"""Shared helpers for the Streamlit dashboard (Stage 09)."""
from __future__ import annotations

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
        return f"Rs {float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def price_lookup_factory(market_db="data/market_data.sqlite3"):
    from market_data.prices import get_stock_price
    def _lookup(sid, d):
        return get_stock_price(sid, str(d), db_path=market_db)
    return _lookup
