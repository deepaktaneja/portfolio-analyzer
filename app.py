"""ICICI Direct portfolio dashboard (Stage 09)."""
from __future__ import annotations

import os

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


# Load data
if "txs" not in st.session_state:
    st.session_state["txs"] = load_transactions()
if "holdings" not in st.session_state:
    st.session_state["holdings"] = load_holdings_df()
txs = st.session_state["txs"]
holdings = st.session_state["holdings"]
pl = price_lookup_factory()

# Global filters
with st.sidebar:
    st.header("Filters")
    _ds = sorted({(t.get("trade_date") or t.get("settlement_date")) for t in txs if (t.get("trade_date") or t.get("settlement_date"))})
    _d0 = _ds[0] if _ds else None
    _d1 = _ds[-1] if _ds else None
    start_date = st.date_input("Start date", value=_d0)
    end_date = st.date_input("End date", value=_d1)
    stock_status = st.selectbox("Stocks", ["all", "current", "exited"], index=0)
    div_mode = st.selectbox("Dividend view", ["gross", "net"], index=0)

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
    from market_data.repository import MarketDataStore
    _md = MarketDataStore("data/market_data.sqlite3")
    _mdr = _md.get_market_data_quality_report()
    if not _mdr.get("securities"):
        st.warning("No market data imported. TWR/unrealized values will be unavailable. Import prices in Data Management.")
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
        from src.store import SQLiteStore
        _store = SQLiteStore(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3"))
        openings = _store.get_opening_positions()
        hist = get_transaction_history(txs, sel)
        st.dataframe(pd.DataFrame(hist), hide_index=True, width="stretch")
        try:
            qty = get_stock_quantity(txs, sel, "2099-12-31", opening_positions=openings)
        except ValueError:
            qty = 0.0
            st.warning("Negative holdings detected for this stock; opening position may be missing.")
        st.metric("Current Quantity", qty)
        res = calculate_stock_xirr(txs, security_id=sel, end_date=end_date, price_lookup=pl)
        _x = res.get("xirr")
        st.metric("Stock XIRR", f"{_x*100:.2f}%" if _x is not None else "N/A")
        summary = get_stock_profit_summary(txs, security_id=sel, as_of_date=end_date, price_lookup=pl)
        st.json(summary)

# Annual Performance
tab = tabs[2]
with tab:
    st.subheader("Annual Performance")
    from market_data.repository import MarketDataStore
    _mdr = MarketDataStore("data/market_data.sqlite3").get_market_data_quality_report()
    if not _mdr.get("securities"):
        st.warning("No market data imported; TWR/unrealized values unavailable.")
    from performance.benchmarks import calculate_annual_returns
    from src.store import SQLiteStore as _SS
    _o = _SS(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3")).get_opening_positions()
    rows = calculate_annual_returns(txs, price_lookup=pl, start_date=start_date, end_date=end_date, frequency="calendar", opening_positions=_o)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

# Benchmark Analysis
tab = tabs[3]
with tab:
    st.subheader("Benchmark Analysis")
    from performance.benchmarks import compare_portfolio_benchmarks
    from src.store import SQLiteStore as _SS
    _o = _SS(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3")).get_opening_positions()
    res = compare_portfolio_benchmarks(txs, price_lookup=pl, start_date=start_date, end_date=end_date, opening_positions=_o)
    st.json(res)

# Gains & Dividends
tab = tabs[4]
with tab:
    st.subheader("Gains & Dividends")
    from accounting.fifo import calculate_realized_gains, calculate_unrealized_gains
    rg = calculate_realized_gains(txs, start_date=start_date, end_date=end_date)
    ug = calculate_unrealized_gains(txs, as_of_date=end_date, price_lookup=pl)
    st.dataframe(pd.DataFrame(rg), hide_index=True, width="stretch")
    st.dataframe(pd.DataFrame(ug), hide_index=True, width="stretch")

# Data Management
tab = tabs[5]
with tab:
    st.subheader("Data Management")
    from src.store import SQLiteStore
    store = SQLiteStore(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3"))
    st.write("Transactions loaded:", len(txs))
    st.write("Holdings:", 0 if holdings is None else len(holdings))
    st.subheader("Opening Positions")
    from ledger.opening import infer_minimum_opening
    inferred = infer_minimum_opening(txs)
    if inferred:
        st.warning("Missing opening holdings detected for some stocks.")
        st.dataframe(pd.DataFrame([{"Stock": k, "Minimum Opening Qty": v, "Status": "Provisional"} for k, v in inferred.items()]), hide_index=True, width="stretch")
        if st.button("Infer opening positions (provisional)"):
            for isin, qty in inferred.items():
                store.add_opening_position(isin, "2018-01-01", qty, source="inferred", verification_status="inferred")
            st.success("Inferred opening positions added (provisional).")
            st.rerun()
    # manual entry
    with st.form("opening_form"):
        isin_in = st.text_input("ISIN")
        qty_in = st.number_input("Opening Qty", min_value=0.0)
        date_in = st.date_input("Ledger Opening Date")
        if st.form_submit_button("Add Opening Position"):
            store.add_opening_position(isin_in, str(date_in), qty_in, source="manual", verification_status="verified")
            st.success("Opening position added.")
            st.rerun()
    ops = store.get_opening_positions()
    if ops:
        st.dataframe(pd.DataFrame(ops), hide_index=True, width="stretch")
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

    with st.form("opening_form"):
        isin_in = st.text_input("ISIN")
        qty_in = st.number_input("Opening Qty", min_value=0.0)
        date_in = st.date_input("Ledger Opening Date")
        if st.form_submit_button("Add Opening Position"):
            store.add_opening_position(isin_in, str(date_in), qty_in, source="manual", verification_status="verified")
            st.success("Opening position added.")
