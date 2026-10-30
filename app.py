"""ICICI Direct XIRR calculator app."""
from __future__ import annotations

import os
from datetime import date

import pandas as pd
import streamlit as st

from pathlib import Path

from src.holdings import current_value_for, discover_portfolio_files, load_holdings
from src.pdf_parser import detect_duplicate_details, detect_missing_quarters, filter_transactions_with_buy_details, parse_folder
from src.reconcile import reconcile
from src.store import SQLiteStore
from src.xirr import annualize_pct, compute_xirr

st.set_page_config(page_title="ICICI XIRR", layout="wide")
st.title("ICICI Direct XIRR Calculator")
st.caption("Parse quarterly transaction-statement PDFs + portfolio XLS to compute XIRR.")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


def _default_statement_folder() -> str:
    return str((DATA_DIR / "statements").resolve())


def _point_to_point_pct(flows: list[tuple]) -> float | None:
    invested = sum(-f[1] for f in flows if f[1] < 0)
    proceeds = sum(f[1] for f in flows if f[1] > 0)
    if invested <= 0:
        return None
    return round((proceeds / invested - 1) * 100, 2)


def _default_portfolio_folder() -> str:
    return str((DATA_DIR / "portfolio").resolve())


folder = st.text_input("Folder containing transaction statement PDFs", value=_default_statement_folder())
portfolio_folder = st.text_input("Folder containing portfolio files (XLS/XLSX)", value=_default_portfolio_folder())
xls_file = st.file_uploader("Portfolio XLS (current holdings) - optional override", type=["xls", "xlsx"])
start_date = st.date_input("XIRR start date", value=date(2020, 1, 1))
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

    # Validate quarters and overlapping/duplicate statement files.
    duplicate_info = detect_duplicate_details(transactions)
    ignored_files = sorted({d["ignored_file"] for d in duplicate_info})
    if ignored_files:
        st.warning("Sorted ignored duplicate/overlapping files: " + ", ".join(ignored_files))
        for d in duplicate_info:
            st.warning(
                f"Ignored file {d['ignored_file']} "
                f"(overlaps with {', '.join(d['conflicts_with'])} "
                f"for {', '.join(d['keys'])})"
            )
        ignored_set = set(ignored_files)
        for f in ignored_files:
            store.remove_file(f)
        transactions = [t for t in transactions if t.get("source_file") not in ignored_set]
    missing = detect_missing_quarters(transactions)
    if missing:
        st.warning("Missing transactions for quarters: " + ", ".join(missing))
    # Ignore sells without matching buy details
    filtered_tx, ignored_buy = filter_transactions_with_buy_details(transactions)
    if ignored_buy:
        st.warning(f"Ignored {len(ignored_buy)} transactions without matching buy details.")
        isins_ignored = sorted({t.get('isin') for t in ignored_buy if t.get('isin')})
        if isins_ignored:
            st.info("Affected ISINs: " + ", ".join(isins_ignored))
    transactions = filtered_tx
    # Apply XIRR start date
    if start_date is not None:
        transactions = [
            t for t in transactions
            if t.get("settlement_date") and pd.to_datetime(t["settlement_date"]).date() >= start_date
        ]
    if not transactions:
        st.error("No valid transactions remain after removing duplicate/overlapping files.")
        st.stop()
    tx_df = pd.DataFrame(transactions)

    holdings = None
    if os.path.isdir(portfolio_folder):
        try:
            holdings = load_holdings(portfolio_folder)
            st.info(f"Loaded {len(holdings)} holdings from portfolio folder.")
        except Exception as e:
            st.warning(f"Could not load holdings from portfolio folder: {e}")
    if holdings is None and xls_file is not None:
        tmp = "/tmp/_portfolio.xls"
        with open(tmp, "wb") as f:
            f.write(xls_file.read())
        holdings = load_holdings(tmp)
        st.info(f"Loaded {len(holdings)} holdings from uploaded XLS.")

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
        if symbol is None:
            sec_rows = [t for t in transactions if t.get("isin") == isin and t.get("security")]
            if sec_rows:
                symbol = sec_rows[0].get("security")
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
         "Point-to-point (%)": _point_to_point_pct(flows),
        })
        all_flows.extend(flows)

    # Portfolio XIRR: aggregate all flows (terminal values already included per ISIN)
    port_x = compute_xirr(all_flows)

    # Annual point-to-point XIRR (calendar years)
    from collections import defaultdict

    annual_flows: dict = defaultdict(list)
    for d, amt in all_flows:
        annual_flows[pd.to_datetime(d).year].append((d, amt))
    annual_rows = []
    for y in range(2020, 2027):
        fy = annual_flows.get(y, [])
        annual_rows.append({
            "Year": y,
            "XIRR (%)": annualize_pct(compute_xirr(fy)),
            "Point-to-point (%)": _point_to_point_pct(fy),
            "Flows": len(fy),
        })
    annual_df = pd.DataFrame(annual_rows)

    # Display
    col1, col2, col3 = st.columns(3)
    col1.metric("Portfolio XIRR", f"{annualize_pct(port_x)}%" if port_x is not None else "N/A")
    col2.metric("Securities", len(isins))
    col3.metric("Portfolio point-to-point", f"{_point_to_point_pct(all_flows)}%" if _point_to_point_pct(all_flows) is not None else "N/A")

    res_df = pd.DataFrame(rows)
    st.dataframe(res_df, hide_index=True, width="stretch")
    st.download_button("Download CSV", data=res_df.to_csv(index=False), file_name="xirr_results.csv", mime="text/csv")

    with st.expander("Annual point-to-point XIRR (calendar years)"):
        st.dataframe(annual_df, hide_index=True, width="stretch")

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