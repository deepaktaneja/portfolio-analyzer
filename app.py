"""ICICI Direct portfolio dashboard (Stage 09)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.shared import (
    inr,
    load_holdings_df,
    load_transactions,
    price_lookup_factory,
)

st.set_page_config(page_title="ICICI Portfolio Analyzer", layout="wide")
st.title("ICICI Direct Portfolio Analyzer")

# Global filters
with st.sidebar:
    st.header("Filters")
    start_date = st.date_input("Start date", value=None)
    end_date = st.date_input("End date", value=None)
    stock_status = st.selectbox("Stocks", ["all", "current", "exited"], index=0)
    div_mode = st.selectbox("Dividend view", ["gross", "net"], index=0)

# Load data
if "txs" not in st.session_state:
    st.session_state["txs"] = load_transactions()
if "holdings" not in st.session_state:
    st.session_state["holdings"] = load_holdings_df()
txs = st.session_state["txs"]
holdings = st.session_state["holdings"]
pl = price_lookup_factory()

tabs = st.tabs(["Overview", "Stock Analysis", "Annual Performance", "Benchmark Analysis", "Gains & Dividends", "Data Management"])

# Overview
tab = tabs[0]
with tab:
    st.subheader("Overview")
    total_val = 0.0
    if holdings is not None and not holdings.empty:
        for _, r in holdings.iterrows():
            cv = r.get("current_value")
            if cv is not None:
                total_val += float(cv)
    st.metric("Current Portfolio Value", inr(total_val))
    st.metric("Transactions", len(txs))
    st.metric("Securities", len({t.get("isin") for t in txs if t.get("isin")}))
    if holdings is not None:
        st.dataframe(holdings, hide_index=True, width="stretch")

# Stock Analysis
tab = tabs[1]
with tab:
    st.subheader("Stock Analysis")
    isins = sorted({t.get("isin") for t in txs if t.get("isin")})
    sel = st.selectbox("Stock", isins) if isins else None
    if sel:
        from accounting.fifo import get_stock_profit_summary
        from ledger.positions import get_stock_quantity, get_transaction_history
        from performance.xirr import calculate_stock_xirr
        hist = get_transaction_history(txs, sel)
        st.dataframe(pd.DataFrame(hist), hide_index=True, width="stretch")
        qty = get_stock_quantity(txs, sel, "2099-12-31")
        st.metric("Current Quantity", qty)
        res = calculate_stock_xirr(txs, security_id=sel, end_date="2025-01-01", price_lookup=pl)
        st.metric("Stock XIRR", str(res.get("xirr")))
        summary = get_stock_profit_summary(txs, security_id=sel, as_of_date="2025-01-01", price_lookup=pl)
        st.json(summary)

# Annual Performance
tab = tabs[2]
with tab:
    st.subheader("Annual Performance")
    from performance.benchmarks import calculate_annual_returns
    rows = calculate_annual_returns(txs, price_lookup=pl, start_date="2024-01-01", end_date="2024-12-31", frequency="calendar")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

# Benchmark Analysis
tab = tabs[3]
with tab:
    st.subheader("Benchmark Analysis")
    from performance.benchmarks import compare_portfolio_benchmarks
    res = compare_portfolio_benchmarks(txs, price_lookup=pl, start_date="2024-01-01", end_date="2024-12-31")
    st.json(res)

# Gains & Dividends
tab = tabs[4]
with tab:
    st.subheader("Gains & Dividends")
    from accounting.fifo import calculate_realized_gains, calculate_unrealized_gains
    rg = calculate_realized_gains(txs, start_date="2024-01-01", end_date="2024-12-31")
    ug = calculate_unrealized_gains(txs, as_of_date="2025-01-01", price_lookup=pl)
    st.dataframe(pd.DataFrame(rg), hide_index=True, width="stretch")
    st.dataframe(pd.DataFrame(ug), hide_index=True, width="stretch")

# Data Management
tab = tabs[5]
with tab:
    st.subheader("Data Management")
    st.write("Transactions loaded:", len(txs))
    st.write("Holdings:", 0 if holdings is None else len(holdings))
    from src.pdf_parser import detect_duplicate_details, detect_missing_quarters
    missing = detect_missing_quarters(txs)
    if missing:
        st.warning("Missing quarters: " + ", ".join(missing))
    dup = detect_duplicate_details(txs)
    if dup:
        st.warning("Duplicate/overlapping files detected.")
        st.dataframe(pd.DataFrame(dup), hide_index=True, width="stretch")
    from src.reconcile import reconcile
    if holdings is not None:
        mism = reconcile(txs, holdings)
        if mism:
            st.warning("Reconciliation mismatches found.")
            st.dataframe(pd.DataFrame(mism), hide_index=True, width="stretch")
        else:
            st.success("Holdings reconcile.")
