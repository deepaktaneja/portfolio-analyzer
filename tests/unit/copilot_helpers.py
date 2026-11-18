"""Shared synthetic portfolio fixtures for Stage 12 copilot tests."""
from __future__ import annotations

from datetime import date

import pandas as pd

from app.context import AppContext, Filters
from copilot.context import build_copilot_context

CONFIG_OFFLINE = {
    "llm": {"enabled": False, "narrate": False, "intent_fallback": False},
    "security": {"max_input_chars": 2000, "rate_limit_per_minute": 100, "audit": False},
    "history": {"max_turns": 20},
    "tools": {"enabled": [
        "portfolio_summary", "holdings", "xirr", "allocation", "concentration", "gains",
        "dividends", "rebalance_plan", "trim_simulation", "overlap",
        "replacement_simulation", "signals", "explain_signal", "research_report",
    ]},
}

PRICES = {"INE001": 6000.0, "INE002": 4000.0}


def make_holdings() -> pd.DataFrame:
    return pd.DataFrame([
        {"isin": "INE001", "symbol": "RELIANCE", "quantity": 100, "avg_price": 4000.0,
         "current_price": 6000.0, "current_value": 600000.0, "sector": "Energy", "market_cap": "Large"},
        {"isin": "INE002", "symbol": "TCS", "quantity": 100, "avg_price": 3000.0,
         "current_price": 4000.0, "current_value": 400000.0, "sector": "Technology", "market_cap": "Large"},
    ])


def make_txs() -> list[dict]:
    return [
        {"isin": "INE001", "buy_sell": "BUY", "trade_date": "2023-01-01", "quantity": 100, "net_amount": 400000.0},
        {"isin": "INE002", "buy_sell": "BUY", "trade_date": "2023-01-01", "quantity": 100, "net_amount": 300000.0},
    ]


def price_lookup(sid, d=None):
    return PRICES.get(str(sid).strip().upper())


def make_app_ctx() -> AppContext:
    filters = Filters(start_date=date(2023, 1, 1), end_date=date(2025, 1, 1), asset_type="Direct Equity")
    return AppContext(txs=make_txs(), holdings=make_holdings(), pl=price_lookup, filters=filters)


def make_copilot_ctx():
    return build_copilot_context(make_app_ctx())
