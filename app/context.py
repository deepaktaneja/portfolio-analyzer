"""Shared application context and global filters.

Builds data once per session (cached in ``st.session_state``) so navigating
between the 5 pages does not trigger unnecessary reloads. All page modules
receive a single :class:`AppContext` instance.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import streamlit as st

from app.shared import (
    compute_dividend_cashflows,
    load_holdings_df,
    load_transactions,
    price_lookup_factory,
)

DEFAULT_MARKET_DB = os.environ.get("ICICI_MARKET_DATA_DB_PATH", "data/market_data.sqlite3")
DEFAULT_XIRR_DB = os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3")

ASSET_TYPES = ["All", "Direct Equity", "Mutual Funds"]


@dataclass
class Filters:
    """User-selected global filters shared across all pages."""

    start_date: date | None = None
    end_date: date | None = None
    asset_type: str = "All"  # one of ASSET_TYPES
    stock_status: str = "all"  # all | current | exited
    dividend_mode: str = "gross"  # gross | net
    person_id: int | None = None
    person_name: str | None = None

    @property
    def show_equity(self) -> bool:
        return self.asset_type in ("All", "Direct Equity")

    @property
    def show_mf(self) -> bool:
        return self.asset_type in ("All", "Mutual Funds")


@dataclass
class AppContext:
    """Everything a page needs to render."""

    txs: list[dict[str, Any]] = field(default_factory=list)
    holdings: Any = None
    pl: Any = None  # price lookup callable
    filters: Filters = field(default_factory=Filters)
    market_db_path: str = DEFAULT_MARKET_DB
    xirr_db_path: str = DEFAULT_XIRR_DB
    dividend_flows: list[dict[str, Any]] = field(default_factory=list)
    corporate_actions: list[Any] = field(default_factory=list)
    opening_positions: list[Any] = field(default_factory=list)


def _date_bounds(txs: list[dict[str, Any]]) -> tuple[date | None, date | None]:
    ds = sorted(
        {
            (t.get("trade_date") or t.get("settlement_date"))
            for t in txs
            if (t.get("trade_date") or t.get("settlement_date"))
        }
    )
    if not ds:
        return None, None

    def _to_date(v):
        if isinstance(v, date):
            return v
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            return None

    return _to_date(ds[0]), _to_date(ds[-1])


def _xirr_db() -> str:
    return os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3").replace("sqlite:///", "", 1)


def build_context(filters: Filters) -> AppContext:
    """Load data (cached in session_state) and assemble the AppContext."""
    pid = filters.person_id
    cache = st.session_state.setdefault("ctx_cache", {})
    tk = ("txs", pid)
    hk = ("holdings", pid)
    if tk not in cache:
        cache[tk] = load_transactions(db_path=_xirr_db(), person_id=pid)
    if hk not in cache:
        cache[hk] = load_holdings_df(person_id=pid)

    txs = cache[tk]
    holdings = cache[hk]

    # Dividends (converted to total cashflows using held quantity at ex-date).
    # Each flow carries gross/tax/net; the Portfolio page picks the amount XIRR uses.
    dk = ("dividend_flows", pid)
    if dk not in cache:
        try:
            from market_data.repository import MarketDataStore

            events = MarketDataStore(DEFAULT_MARKET_DB).get_dividends()
            cache[dk] = compute_dividend_cashflows(txs, events)
        except Exception:  # noqa: BLE001
            cache[dk] = []

    # Corporate actions (for rights subscription cashflows in XIRR).
    ak = ("corporate_actions", pid)
    if ak not in cache:
        try:
            from market_data.repository import MarketDataStore

            cache[ak] = MarketDataStore(DEFAULT_MARKET_DB).get_corporate_actions()
        except Exception:  # noqa: BLE001
            cache[ak] = []

    # Opening positions (ledger carry-forward quantities).
    ok = ("opening_positions", pid)
    if ok not in cache:
        try:
            from src.store import SQLiteStore

            cache[ok] = SQLiteStore(_xirr_db()).get_opening_positions(person_id=pid)
        except Exception:  # noqa: BLE001
            cache[ok] = []

    dividend_flows = cache[dk]
    corporate_actions = cache[ak]
    opening_positions = cache[ok]

    pl = price_lookup_factory(
        market_db=DEFAULT_MARKET_DB,
        start_date=filters.start_date,
        end_date=filters.end_date,
        holdings=holdings,
    )
    return AppContext(
        txs=txs,
        holdings=holdings,
        pl=pl,
        filters=filters,
        market_db_path=DEFAULT_MARKET_DB,
        xirr_db_path=DEFAULT_XIRR_DB,
        dividend_flows=dividend_flows,
        corporate_actions=corporate_actions,
        opening_positions=opening_positions,
    )


def default_dates(txs: list[dict[str, Any]]) -> tuple[date | None, date | None]:
    """Expose the transaction date bounds for the sidebar filter defaults."""
    return _date_bounds(txs)


def filter_year_range(txs: list[dict[str, Any]]) -> tuple[int, int]:
    """First/last calendar year covered by the imported transactions.

    The sidebar only offers annual boundaries, which keeps price lookups limited
    to year ends. The earliest selectable year comes from the data itself.
    """
    first, last = _date_bounds(txs)
    today = date.today()
    start_year = first.year if first else today.year
    end_year = max(last.year if last else today.year, today.year)
    if start_year > end_year:
        start_year = end_year
    return start_year, end_year


def yearly_start_dates(txs: list[dict[str, Any]]) -> list[date]:
    """1 January of every selectable year (first transaction year .. today)."""
    start_year, end_year = filter_year_range(txs)
    return [date(year, 1, 1) for year in range(start_year, end_year + 1)]


def yearly_end_dates(txs: list[dict[str, Any]]) -> list[date]:
    """31 December of every completed year, plus today for the running year."""
    start_year, end_year = filter_year_range(txs)
    today = date.today()
    options = {
        date(year, 12, 31)
        for year in range(start_year, end_year + 1)
        if date(year, 12, 31) <= today
    }
    options.add(today)
    return sorted(options)


def default_end_date(txs: list[dict[str, Any]]) -> date:
    """Last transaction year end, or today when the data reaches the current year."""
    _, last = _date_bounds(txs)
    today = date.today()
    if last is None or last.year >= today.year:
        return today
    return date(last.year, 12, 31)
