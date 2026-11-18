"""Investments Portfolio Analyzer — 5-page UI (refactored).

Navigation:
  1. Portfolio           — consolidated Equity + MF overview & allocation
  2. Performance         — annual/historical performance, benchmarks, gains, dividends
  3. Holdings & Research — unified holdings table, detail panel, AI research
  4. Backtesting         — stock-replacement simulator (actual vs hypothetical)
  5. Data & Settings     — imports, reconciliation, market data, MCP & settings

All backend services, calculations and data are reused unchanged. UI logic lives
in ``app/pages/`` with shared components in ``app/components/``.
"""
from __future__ import annotations

import os

import streamlit as st

from app.components.filters import render_sidebar_filters
from app.context import build_context
from app.pages import backtesting, data_settings, holdings_research, performance, portfolio
from app.shared import load_transactions

st.set_page_config(page_title="ICICI Portfolio Analyzer", layout="wide")

# ---- Optional authentication (env-gated; no-op unless APP_AUTH_ENABLED) -----
from app.auth import require_auth

require_auth()

MARKET_DB_PATH = os.environ.get("ICICI_MARKET_DATA_DB_PATH", "data/market_data.sqlite3")

# Ensure transactions are loaded once (cached in session_state) so the sidebar
# date defaults and every page can reuse them without reloading.
if "all_txs" not in st.session_state:
    st.session_state["all_txs"] = load_transactions()
txs = st.session_state["all_txs"]

# ---- Global sidebar filters (shared across all pages) ----------------------
filters = render_sidebar_filters(txs)

# ---- Page navigation -------------------------------------------------------
PAGES = {
    "1 · Portfolio": portfolio,
    "2 · Direct Equity": performance,
    "3 · Mutual Funds": holdings_research,
    "4 · Backtesting": backtesting,
    "5 · Data & Settings": data_settings,
}

with st.sidebar:
    st.divider()
    st.header("Navigation")
    page_label = st.radio("Page", list(PAGES.keys()), index=0, key="nav_page")

st.title("Investments Portfolio Analyzer")

# Build the shared context (data loaded once; price lookup built from filters).
ctx = build_context(filters)

# ---- Render the selected page ----------------------------------------------
PAGES[page_label].render(ctx)
