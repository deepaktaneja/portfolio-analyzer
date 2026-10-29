"""ICICI Direct XIRR calculator app."""
from __future__ import annotations

import os
from datetime import date

import pandas as pd
import streamlit as st

from src.holdings import current_value_for, load_holdings
from src.pdf_parser import parse_folder
from src.reconcile import reconcile
from src.store import SQLiteStore
from src.xirr import annualize_pct, compute_xirr

st.set_page_config(page_title="ICICI XIRR", layout="wide")
st.title("ICICI Direct XIRR Calculator")
st.caption("Parse quarterly transaction-statement PDFs + portfolio XLS to compute XIRR.")

folder = st.text_input("Folder containing transaction statement PDFs", value="/Users/anmol/Downloads")
xls_file = st.file_uploader("Portfolio XLS (current holdings)", type=["xls", "xlsx"])
rebuild = st.checkbox("Rebuild from scratch (clear cached data)", value=False)

db_path = os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3")
store = SQLiteStore(db_path)

if st.button("Run analysis", type="primary"):
    if not os.path.isdir(folder):
        st.error("Folder not found.")
        st.stop()
    if rebuild:
        store.clear()
        st.info("Cache cleared.")
    known_before = len(store.known_files())
    transactions = parse_folder(folder, store=store)
    new_files = len(store.known_files()) - known_before
    if not transactions:
        st.error("No transactions parsed from PDFs in folder.")
        st.stop()
    tx_df = pd.DataFrame(transactions)
    st.success(f"Parsed {len(tx_df)} transactions. New files this run: {max(new_files,0)}. Total cached files: {len(store.known_files())}.")

    holdings = None
    if xls_file is not None:
        tmp = "/tmp/_portfolio.xls"
        with open(tmp, "wb") as f:
            f.write(xls_file.read())
        holdings = load_holdings(tmp)
        st.info(f"Loaded {len(holdings)} holdings from XLS.")

    # Build per-ISIN cash flows
    analysis_date = date.today()
    isins = sorted(set(t["isin"] for t in transactions if t.get("isin")))
    rows = []
    all_flows: list[tuple] = []
    for isin in isins:
        flows: list[tuple] = []
        for t in transactions:
            if t["isin"] != isin:
                continue
            if t["settlement_date"] is None or t["net_amount"] is None:
                continue
            amt = float(t["net_amount"])
            if t["buy_sell"] == "BUY":
                flows.append((t["settlement_date"], -amt))
            elif t["buy_sell"] == "SELL":
                flows.append((t["settlement_date"], amt))
        # terminal value from holdings
        terminal = None
        symbol = None
        if holdings is not None:
            hrow = holdings[holdings["isin"] == isin]
            if not hrow.empty:
                r = hrow.iloc[0]
                symbol = r.get("symbol")
                terminal = current_value_for(r.to_dict())
        if terminal is not None:
            flows.append((analysis_date, terminal))
        x = compute_xirr(flows)
        # totals
        bought = sum(-f[1] for f in flows if f[1] < 0)
        sold = sum(f[1] for f in flows if f[1] > 0 and f[0] != analysis_date)
        terminal_val = terminal or 0
        rows.append({
            "ISIN": isin,
            "Symbol": symbol,
            "Transactions": sum(1 for t in transactions if t["isin"] == isin),
            "Bought (Rs)": round(bought, 2),
            "Sold (Rs)": round(sold, 2),
            "Terminal value (Rs)": round(terminal_val, 2),
            "XIRR (%)": annualize_pct(x),
        })
        all_flows.extend(flows)

    # Portfolio XIRR: aggregate all flows (terminal values already included per ISIN)
    port_x = compute_xirr(all_flows)

    # Display
    col1, col2 = st.columns(2)
    col1.metric("Portfolio XIRR", f"{annualize_pct(port_x)}%" if port_x is not None else "N/A")
    col2.metric("Securities", len(isins))

    res_df = pd.DataFrame(rows)
    st.dataframe(res_df, hide_index=True, width="stretch")
    st.download_button("Download CSV", data=res_df.to_csv(index=False), file_name="xirr_results.csv", mime="text/csv")

    # Reconciliation
    if holdings is not None:
        mism = reconcile(transactions, holdings)
        if mism:
            st.subheader("Holdings reconciliation mismatches")
            st.dataframe(pd.DataFrame(mism), hide_index=True, width="stretch")
        else:
            st.success("Holdings reconcile with transaction statements.")

    with st.expander("Raw parsed transactions"):
        st.dataframe(tx_df, hide_index=True, width="stretch")