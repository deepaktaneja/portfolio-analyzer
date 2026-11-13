"""Investments Portfolio Analyzer (Stage 09)."""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from app.shared import (
    inr,
    load_holdings_df,
    load_transactions,
    price_lookup_factory,
    security_name,
    fmt1,
    fmt_pct,
    format_df_one_decimal,
    tx_hash,
)

st.set_page_config(page_title="ICICI Portfolio Analyzer", layout="wide")
st.title("Investments Portfolio Analyzer")

MARKET_DB_PATH = os.environ.get("ICICI_MARKET_DATA_DB_PATH", "data/market_data.sqlite3")

# Load data
if "txs" not in st.session_state:
    st.session_state["txs"] = load_transactions()
if "holdings" not in st.session_state:
    st.session_state["holdings"] = load_holdings_df()
txs = st.session_state["txs"]
holdings = st.session_state["holdings"]

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

pl = price_lookup_factory(market_db=MARKET_DB_PATH, start_date=start_date, end_date=end_date)

tabs = st.tabs([
    "Overview", "Stock Analysis", "Annual Performance", "Benchmark Analysis",
    "Gains & Dividends", "Data Management", "Market Data",
    "MF Overview", "MF Holdings", "MF XIRR", "MF Reconciliation",
    "Consolidated Overview", "Consolidated Allocation", "Consolidated Performance",
])

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
    _md = MarketDataStore(MARKET_DB_PATH)
    _mdr = _md.get_market_data_quality_report()
    if not _mdr.get("securities"):
        st.warning("No market data imported. TWR/unrealized values will be unavailable. Import prices in Data Management.")
    from src.store import SQLiteStore as _SS
    _openings = _SS(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3")).get_opening_positions()
    from market_data.repository import MarketDataStore as _MDS
    _acts = _MDS(MARKET_DB_PATH).get_corporate_actions()
    from corporate_actions.models import CorporateAction
    from ledger.holdings_service import _parse_date as _pd
    _ca_objs = [CorporateAction(security_id=r["security_id"], event_type=r["event_type"], ex_date=_pd(r.get("ex_date")), effective_date=_pd(r.get("effective_date")), ratio=r.get("ratio"), successor_id=r.get("successor_id"), verified=bool(r.get("verified")), source=r.get("source"), verification_status=r.get("verification_status")) for r in _acts]
    from ledger.holdings_service import get_holdings as _gh
    try:
        _recon = _gh(end_date, transactions=txs, actions=_ca_objs, opening_positions=_openings)
        st.subheader("Reconstructed holdings")
        _rdf = pd.DataFrame([{"isin": k, "reconstructed_qty": v} for k, v in _recon.items()])
        if not _rdf.empty:
            _rdf["security_name"] = _rdf["isin"].map(lambda x: security_name(x, txs, holdings))
        st.dataframe(_rdf, hide_index=True, width="stretch")
    except Exception as e:  # noqa: BLE001
        st.warning(f"Reconstructed holdings unavailable: {e}")
    # broker snapshot
    _snap = {}
    if holdings is not None and not holdings.empty:
        for _, r in holdings.iterrows():
            isin = r.get("isin")
            if isin:
                _snap[str(isin).strip().upper()] = float(r.get("quantity") or 0)
    from ledger.holdings_service import get_holdings_reconciliation as _gr
    try:
        _rows = _gr(end_date, transactions=txs, actions=_ca_objs, opening_positions=_openings, snapshot=_snap)
        if _rows:
            st.subheader("Reconstructed vs broker quantities (differences)")
            st.dataframe(pd.DataFrame(_rows), hide_index=True, width="stretch")
        else:
            st.success("Reconstructed and broker quantities reconcile.")
    except Exception as e:  # noqa: BLE001
        st.warning(f"Reconciliation unavailable: {e}")
    if holdings is not None:
        st.dataframe(holdings, hide_index=True, width="stretch")

# Stock Analysis
tab = tabs[1]
with tab:
    st.subheader("Stock Analysis")
    isins = sorted({t.get("isin") for t in txs if t.get("isin")})
    sel = st.selectbox("Stock", isins) if isins else None
    if sel:
        st.write("Security name:", security_name(sel, txs, holdings))
        from accounting.fifo import get_stock_profit_summary
        from ledger.holdings_service import get_stock_quantity as get_qty_actions
        from ledger.positions import get_transaction_history
        from performance.xirr import calculate_stock_xirr
        from src.store import SQLiteStore
        _store = SQLiteStore(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3"))
        openings = _store.get_opening_positions()
        hist = get_transaction_history(txs, sel)
        st.dataframe(pd.DataFrame(hist), hide_index=True, width="stretch")
        from market_data.repository import MarketDataStore
        _actions = MarketDataStore(MARKET_DB_PATH).get_corporate_actions()
        try:
            qty = get_qty_actions(txs, sel, "2099-12-31", opening_positions=openings, actions=_actions)
        except ValueError:
            qty = 0.0
            st.warning("Negative holdings detected for this stock; opening position may be missing.")
        st.metric("Current Quantity", qty)
        # reconstructed vs broker quantity
        _broker_qty = None
        if holdings is not None and not holdings.empty:
            _m = holdings[holdings["isin"].astype(str).str.upper() == str(sel).strip().upper()]
            if not _m.empty:
                _broker_qty = float(_m.iloc[0].get("quantity") or 0)
        if _broker_qty is not None:
            st.metric("Broker Quantity", _broker_qty)
            st.metric("Difference (reconstructed - broker)", qty - _broker_qty)
        res = calculate_stock_xirr(txs, security_id=sel, end_date=end_date, price_lookup=pl)
        _x = res.get("xirr")
        st.metric("Stock XIRR", fmt_pct(_x))
        if _x is None and res.get("reason"):
            st.warning(f"XIRR unavailable: {res.get("reason")}")
        summary = get_stock_profit_summary(txs, security_id=sel, as_of_date=end_date, price_lookup=pl)
        st.json(summary)

# Annual Performance
tab = tabs[2]
with tab:
    st.subheader("Annual Performance")
    from market_data.repository import MarketDataStore
    _mdr = MarketDataStore(MARKET_DB_PATH).get_market_data_quality_report()
    if not _mdr.get("securities"):
        st.warning("No market data imported; TWR/unrealized values unavailable.")
    from market_data.prices import get_index_tri as _get_idx
    def _idx50(d):
        try:
            return _get_idx("NIFTY50TRI", str(d), db_path=MARKET_DB_PATH)
        except Exception:
            return None
    def _idx500(d):
        try:
            return _get_idx("NIFTY500TRI", str(d), db_path=MARKET_DB_PATH)
        except Exception:
            return None
    from performance.benchmarks import calculate_annual_returns
    from src.store import SQLiteStore as _SS
    _o = _SS(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3")).get_opening_positions()
    _cache_key = ("annual", str(start_date), str(end_date), tx_hash(txs), tx_hash(_o) if _o else "")
    _cached_rows = st.session_state.get(_cache_key)
    if _cached_rows is None:
        try:
            with st.spinner("Calculating annual performance..."):
                _cached_rows = calculate_annual_returns(txs, price_lookup=pl, index_lookup_50=_idx50, index_lookup_500=_idx500, start_date=start_date, end_date=end_date, frequency="calendar", opening_positions=_o)
            st.session_state[_cache_key] = _cached_rows
        except Exception as e:
            st.error(f"Annual Performance unavailable: {e}")
            _cached_rows = None
    if _cached_rows is not None:
        try:
            st.dataframe(pd.DataFrame(_cached_rows), hide_index=True, width="stretch")
            if _cached_rows and all(r.get("portfolio_twr") is None for r in _cached_rows):
                st.warning("TWR unavailable: missing price coverage for the selected period.")
            if not any(r.get("nifty50_tri_return") is not None for r in _cached_rows):
                st.warning("Nifty TRI data not available. Import Nifty TRI history to see benchmark returns.")
        except Exception as e:
            st.error(f"Annual Performance unavailable: {e}")

# Benchmark Analysis
tab = tabs[3]
with tab:
    st.subheader("Benchmark Analysis")
    try:
        from market_data.prices import get_benchmark_sync_status, sync_benchmark_history
        for _iid in ("NIFTY50TRI", "NIFTY500TRI"):
            st.write(_iid, get_benchmark_sync_status(_iid, db_path=MARKET_DB_PATH))
        if st.button("Sync Nifty TRI (last 5 years)", key="sync_tri_bench"): 
            from datetime import date, timedelta
            _end = date.today()
            _start = _end.replace(year=_end.year-5)
            for _iid in ("NIFTY50TRI", "NIFTY500TRI"):
                try:
                    st.json(sync_benchmark_history(_iid, str(_start), str(_end), db_path=MARKET_DB_PATH))
                except Exception as e:
                    st.error(str(e))
            st.rerun()
    except Exception as e:
        st.warning(str(e))
    from market_data.prices import get_index_tri as _get_idx
    def _idx50(d):
        try:
            return _get_idx("NIFTY50TRI", str(d), db_path=MARKET_DB_PATH)
        except Exception:
            return None
    def _idx500(d):
        try:
            return _get_idx("NIFTY500TRI", str(d), db_path=MARKET_DB_PATH)
        except Exception:
            return None
    from performance.benchmarks import compare_portfolio_benchmarks
    from src.store import SQLiteStore as _SS
    _o = _SS(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3")).get_opening_positions()
    _bkey = ("benchmark", str(start_date), str(end_date), tx_hash(txs), tx_hash(_o) if _o else "")
    _bres = st.session_state.get(_bkey)
    if _bres is None:
        try:
            with st.spinner("Calculating benchmark comparison..."):
                _bres = compare_portfolio_benchmarks(txs, price_lookup=pl, index_lookup_50=_idx50, index_lookup_500=_idx500, start_date=start_date, end_date=end_date, opening_positions=_o)
            st.session_state[_bkey] = _bres
        except Exception as e:
            st.error(f"Benchmark Analysis unavailable: {e}")
            _bres = None
    if _bres is not None:
        try:
            st.json(_bres)
            _missing_tri = []
            for _iid in ("NIFTY50TRI", "NIFTY500TRI"):
                try:
                    if _get_idx(_iid, str(end_date)) is None:
                        _missing_tri.append(_iid)
                except Exception:  # noqa: BLE001
                    _missing_tri.append(_iid)
            if _missing_tri:
                st.warning("Missing benchmark TRI data: " + ", ".join(_missing_tri))
                st.info(f"Import TRI history covering {start_date} to {end_date} to enable benchmark comparison.")
            if _bres.get("methodology_time_weighted", {}).get("nifty50_tri_return") is None:
                st.warning("Benchmark TRI data not available. Import Nifty TRI history to see benchmark returns.")
        except Exception as e:
            st.error(f"Benchmark Analysis unavailable: {e}")

# Gains & Dividends
tab = tabs[4]
with tab:
    st.subheader("Gains & Dividends")
    from accounting.fifo import calculate_realized_gains, calculate_unrealized_gains
    from market_data.repository import MarketDataStore as _MDS
    _acts = _MDS(MARKET_DB_PATH).get_corporate_actions()
    # convert to CorporateAction objects
    from corporate_actions.models import CorporateAction
    def _conv(r):
        from ledger.holdings_service import _parse_date
        return CorporateAction(security_id=r["security_id"], event_type=r["event_type"], ex_date=_parse_date(r.get("ex_date")), effective_date=_parse_date(r.get("effective_date")), ratio=r.get("ratio"), successor_id=r.get("successor_id"), verified=bool(r.get("verified")), source=r.get("source"), verification_status=r.get("verification_status"))
    _ca_objs = [_conv(r) for r in _acts]
    try:
        from corporate_actions.engine import _dedupe_actions
        _ca_objs = _dedupe_actions(_ca_objs)
    except Exception:  # noqa: BLE001
        pass
    try:
        with st.spinner("Calculating gains..."):
            rg = calculate_realized_gains(txs, actions=_ca_objs, start_date=start_date, end_date=end_date)
            ug = calculate_unrealized_gains(txs, actions=_ca_objs, as_of_date=end_date, price_lookup=pl)
        if isinstance(rg, list) and rg:
            _rdf = pd.DataFrame(rg)
            if "security_id" in _rdf.columns:
                _rdf["security_name"] = _rdf["security_id"].map(lambda x: security_name(x, txs, holdings))
            st.dataframe(format_df_one_decimal(_rdf), hide_index=True, width="stretch")
        else:
            st.info("No realized gains records available.")
        if isinstance(ug, list) and ug:
            _udf = pd.DataFrame(ug)
            if "security_id" in _udf.columns:
                _udf["security_name"] = _udf["security_id"].map(lambda x: security_name(x, txs, holdings))
            st.dataframe(format_df_one_decimal(_udf), hide_index=True, width="stretch")
        else:
            st.info("No unrealized gains records available.")
        # dividends (not assumed zero)
        try:
            _divs = _MDS(MARKET_DB_PATH).get_dividend_events()
        except Exception as e:  # noqa: BLE001
            _divs = []
            st.warning(f"Dividend records unavailable: {e}")
        if _divs:
            st.subheader("Dividend records")
            st.dataframe(pd.DataFrame(_divs), hide_index=True, width="stretch")
        else:
            st.warning("No dividend records found; dividend income is not assumed to be zero.")
        # cost basis warnings
        if isinstance(ug, list):
            _missing = [r for r in ug if r.get("price") is None]
            if _missing:
                st.warning(f"Missing cost-basis/price data for {len(_missing)} position(s); gains may be provisional.")
        # reconciliation warning
        st.warning("If stock quantities are not reconciled, gains may be provisional.")
    except Exception as e:
        st.error(f"Gains & Dividends unavailable: {e}")

# Data Management
tab = tabs[5]
with tab:
    st.subheader("Data Management")
    from src.store import SQLiteStore
    store = SQLiteStore(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3"))
    st.write("Transactions loaded:", len(txs))
    if not txs:
        st.info("No transactions imported.")
    st.write("Holdings:", 0 if holdings is None else len(holdings))
    if holdings is None or (hasattr(holdings, "empty") and holdings.empty):
        st.info("No holdings statement imported.")
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
    with st.form("opening_form_manual"):
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
    else:
        st.info("No opening positions recorded.")
    # Holdings reconciliation
    st.subheader("Holdings Reconciliation")
    _acts = _MDS(MARKET_DB_PATH).get_corporate_actions()
    from corporate_actions.models import CorporateAction
    def _conv2(r):
        from ledger.holdings_service import _parse_date
        return CorporateAction(security_id=r["security_id"], event_type=r["event_type"], ex_date=_parse_date(r.get("ex_date")), effective_date=_parse_date(r.get("effective_date")), ratio=r.get("ratio"), successor_id=r.get("successor_id"), verified=bool(r.get("verified")), source=r.get("source"), verification_status=r.get("verification_status"))
    _ca_objs = [_conv2(r) for r in _acts]
    openings = store.get_opening_positions()
    # use XLS statement as-of date when available
    _as_of = end_date
    if holdings is not None and getattr(holdings, "attrs", None):
        _as_of = holdings.attrs.get("as_of_date") or _as_of
    snap = {}
    if holdings is not None:
        for _, r in holdings.iterrows():
            isin = str(r.get("isin") or r.get("security") or "").strip().upper()
            if isin:
                snap[isin] = float(r.get("quantity") or 0)
    from ledger.holdings_service import get_holdings_reconciliation
    rows = get_holdings_reconciliation(_as_of, transactions=txs, actions=_ca_objs, opening_positions=openings, snapshot=snap, db_path=MARKET_DB_PATH)
    if rows:
        _rdf = pd.DataFrame(rows)
        if "isin" in _rdf.columns:
            _rdf["security_name"] = _rdf["isin"].map(lambda x: security_name(x, txs, holdings))
        try:
            _styled = _rdf.style.apply(lambda r: ["background-color: #f8d7da" if abs(r.get("diff", 0)) > 1e-9 else "" for _ in r], axis=1)
            st.dataframe(_styled, hide_index=True, width="stretch")
        except Exception:
            st.dataframe(_rdf, hide_index=True, width="stretch")
    else:
        st.success("Holdings reconcile.")
    # reverse-derived candidate openings (provisional)
    try:
        from ledger.holdings_service import get_reverse_opening_candidates
        _cands = get_reverse_opening_candidates(_as_of, transactions=txs, snapshot=snap, actions=_ca_objs, opening_positions=openings)
        if _cands:
            st.subheader("Candidate opening positions (provisional)")
            st.dataframe(pd.DataFrame([{"isin": k, "candidate_opening_qty": v, "status": "provisional"} for k, v in _cands.items()]), hide_index=True, width="stretch")
            if st.button("Add candidate openings (provisional)"):
                for isin, qty in _cands.items():
                    store.add_opening_position(isin, str(_as_of), float(qty), source="reversed_from_xls", verification_status="provisional")
                st.success("Candidate opening positions added (provisional).")
                st.rerun()
    except Exception as e:  # noqa: BLE001
        st.warning(f"Reverse opening candidates unavailable: {e}")
    st.subheader("Quantity drill-down")
    _sel_opts = sorted({r["isin"] for r in rows}) if rows else []
    if not _sel_opts and holdings is not None:
        _sel_opts = sorted(str(r.get("isin")).strip().upper() for _, r in holdings.iterrows() if r.get("isin"))
    sel = st.selectbox("Select security", _sel_opts, key="recon_sel")
    if sel:
        from ledger.holdings_service import get_quantity_movements
        moves = get_quantity_movements(sel, _as_of, transactions=txs, actions=_ca_objs, opening_positions=openings, db_path=MARKET_DB_PATH)
        _mdf = pd.DataFrame(moves)
        if not _mdf.empty:
            _mdf["security_name"] = security_name(sel, txs, holdings)
        st.dataframe(_mdf, hide_index=True, width="stretch")
    
    # Clear all data button
    if st.button("Clear All Data"):
        import shutil, os
        db_paths = ["data/xirr.sqlite3", "data/market_data.sqlite3", "data/portfolio.sqlite3"]
        for db_path in db_paths:
            if os.path.exists(db_path):
                shutil.rmtree(db_path)
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.success("All data cleared. Please reload.")
        st.rerun()
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
# Market Data
tab = tabs[6]
with tab:
    st.subheader("Market Data Manager")
    from market_data.repository import MarketDataStore
    _md = MarketDataStore(MARKET_DB_PATH)
    _mdr = _md.get_market_data_quality_report()
    st.metric("Securities with prices", len(_mdr.get("securities", [])))
    st.metric("Anomalies", len(_mdr.get("anomalies", [])))
    from market_data.fetcher import discover_securities as _discover
    _discovered = _discover(db_path=MARKET_DB_PATH)
    _with_prices = set(_mdr.get("securities", []))
    _missing_prices = [s for s in _discovered if s not in _with_prices]
    st.metric("Securities requiring data", len(_discovered))
    st.metric("Securities missing prices", len(_missing_prices))
    if _missing_prices:
        st.warning("Missing prices for: " + ", ".join(_missing_prices))
    try:
        _cas = _md.get_corporate_actions()
        _unverified = [a for a in _cas if not a.get("verified")]
        st.metric("Corporate actions requiring verification", len(_unverified))
        if _unverified:
            st.dataframe(pd.DataFrame(_unverified), hide_index=True, width="stretch")
    except Exception as e:  # noqa: BLE001
        st.error(f"Corporate actions unavailable: {e}")
    try:
        _divs = _md.get_dividend_events() if hasattr(_md, "get_dividend_events") else []
        st.metric("Dividend records", len(_divs))
        if not _divs:
            st.info("No dividend records available.")
    except Exception as e:  # noqa: BLE001
        st.error(f"Dividend records unavailable: {e}")
    from market_data.prices import get_index_tri as _tri
    for _iid in ("NIFTY50TRI", "NIFTY500TRI"):
        try:
            _v = _tri(_iid, str(end_date), db_path=MARKET_DB_PATH)
            st.write(f"{_iid}: {'available' if _v is not None else 'missing'}")
        except Exception as e:  # noqa: BLE001
            st.warning(f"{_iid} unavailable: {e}")
    runs = _md.get_sync_runs(limit=1)
    if runs:
        st.caption(f"Last sync: {runs[0].get('started_at')} ({runs[0].get('status')})")
    from market_data.fetcher import discover_securities
    from market_data.prices import (
        get_market_data_quality_report,
        get_sync_runs,
        sync_all_market_data,
        sync_missing_prices,
    )
    md = MarketDataStore(MARKET_DB_PATH)
    try:
        report = get_market_data_quality_report(db_path=MARKET_DB_PATH)
        st.write("Discovered securities:", report.get("securities", []))
        st.dataframe(pd.DataFrame([{"security_id": k, "security_name": security_name(k, txs, holdings), **v} for k, v in report.get("stock_coverage", {}).items()]), hide_index=True, width="stretch")
    except Exception as e:
        st.error(f"Market data report unavailable: {e}")
        report = {}
    st.write("Last sync runs:")
    try:
        st.dataframe(pd.DataFrame(get_sync_runs(db_path=MARKET_DB_PATH)), hide_index=True, width="stretch")
    except Exception as e:
        st.error(f"Sync runs unavailable: {e}")
    discovered = discover_securities(db_path=MARKET_DB_PATH)
try:
    from market_data.prices import get_missing_benchmark_dates
    for _iid in ("NIFTY50TRI", "NIFTY500TRI"):
        _missing = get_missing_benchmark_dates(_iid, str(start_date), str(end_date), db_path=MARKET_DB_PATH)
        st.write(_iid+" missing dates:", len(_missing))
except Exception as e:
    st.warning(str(e))

try:
    from market_data.prices import get_benchmark_sync_status
    for _iid in ("NIFTY50TRI", "NIFTY500TRI"):
        st.write(_iid, get_benchmark_sync_status(_iid, db_path=MARKET_DB_PATH))
except Exception as e:
    st.warning(str(e))

    st.write("Discovered from transactions:", discovered)
    c1, c2 = st.columns(2)
    if c1.button("Sync all market data"):
        res = sync_all_market_data(db_path=MARKET_DB_PATH, provider="manual", security_ids=discovered or None)
        st.json(res)
        st.rerun()
    if c2.button("Sync missing prices"):
        from datetime import date, timedelta
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=365)).isoformat()
        res = sync_missing_prices(start, end, db_path=MARKET_DB_PATH, provider="manual", security_ids=discovered or None)
        st.json(res)
        st.rerun()
    st.subheader("Symbol mapping")
    with st.form("symbol_map_form"):
        sid = st.text_input("Security ID")
        sym = st.text_input("Provider symbol")
        prov = st.text_input("Provider", value="yfinance")
        if st.form_submit_button("Save mapping"):
            md.upsert_security_symbol_map([{"security_id": sid, "symbol": sym, "provider_symbol": sym, "provider": prov}])
    st.subheader("Corporate actions")
    try:
        ca_df = pd.DataFrame(md.get_corporate_actions())
        st.dataframe(ca_df, hide_index=True, width="stretch")
    except Exception as e:
        st.error(f"Corporate actions unavailable: {e}")
    with st.form("verify_ca_form"):
        ca_id = st.number_input("Corporate action ID", min_value=1, step=1)
        if st.form_submit_button("Verify"):
            md.verify_corporate_action(int(ca_id), verified=True, verified_by="manual")
            st.success("Verified")
            st.rerun()
    st.subheader("Manual corporate action entry")
    with st.form("manual_ca_form"):
        sid = st.text_input("Security ID")
        et = st.selectbox("Event type", ["split", "bonus", "stock_dividend", "rights", "merger", "demerger", "buyback"])
        exd = st.date_input("Ex date")
        ratio = st.number_input("Ratio", value=1.0)
        succ = st.text_input("Successor ID (merger/demerger)")
    st.subheader("Automated Nifty TRI sync")
    with st.form("tri_sync_form"):
        tri_index_sync = st.selectbox("Index to sync", ["NIFTY50TRI", "NIFTY500TRI"], key="tri_sync_idx")
        tri_start = st.date_input("TRI start date", key="tri_sync_start")
        tri_end = st.date_input("TRI end date", key="tri_sync_end")
        tri_url = st.text_input("Optional CSV/HTML URL (leave blank for Niftyindices endpoint)")
        if st.form_submit_button("Sync TRI"):
            try:
                from market_data.fetcher import sync_index_tri
                res = sync_index_tri(tri_index_sync, start=str(tri_start), end=str(tri_end), db_path=MARKET_DB_PATH, source_url=tri_url or None)
                st.success(f"Synced {res.get('upserted', 0)} TRI rows")
                if res.get("warnings"):
                    st.warning("; ".join(res["warnings"][:10]))
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"TRI sync failed: {e}")
    st.subheader("Dividend import")
    with st.form("dividend_import_form"):
        div_path = st.text_input("Dividend CSV path")
        if st.form_submit_button("Import dividends"):
            try:
                from market_data.fetcher import sync_dividend_events
                res = sync_dividend_events(div_path, db_path=MARKET_DB_PATH)
                st.success(f"Imported {res.get('upserted', 0)} dividend records")
                if res.get("warnings"):
                    st.warning("; ".join(res["warnings"][:10]))
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Dividend import failed: {e}")
        alloc = st.number_input("Cost allocation (merger/demerger)", value=1.0)
        if st.form_submit_button("Add"):
            md.upsert_corporate_actions([{"security_id": sid, "event_type": et, "ex_date": str(exd), "ratio": ratio, "successor_id": succ or None, "cost_allocation": alloc if et in ("merger", "demerger") else None, "verified": True, "verification_status": "manual", "source": "manual"}])
            st.success("Added")
            st.rerun()
            st.success("Saved")
            st.rerun()
    st.subheader("Manual TRI import")
    with st.form("tri_import_form"):
        tri_path = st.text_input("TRI CSV path", value="tests/fixtures/nifty50tri.csv")
        tri_index = st.selectbox("Index", ["NIFTY50TRI", "NIFTY500TRI"])
        if st.form_submit_button("Import TRI"):
            try:
                from market_data.adapters import import_index_tri
                parsed, warnings = import_index_tri(tri_path, tri_index, source="manual")
                md.upsert_index_tri(parsed)
                st.success(f"Imported {len(parsed)} TRI rows")
                if warnings:
                    st.warning("; ".join(warnings[:10]))
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"TRI import failed: {e}")

# ============================================================
# MF Overview Tab
# ============================================================
tab_mf_overview = tabs[7]
with tab_mf_overview:
    st.subheader("MF Portfolio Overview")
    st.info("MF overview requires imported MF statements. Use Data Management tab to import.")

    try:
        from mf.accounting import calculate_fund_xirr, calculate_portfolio_mf_xirr
        from mf.reports import portfolio_mf_report
        from mf.importers import is_mf_statement
        import os

        mf_dir = "data/statements"
        mf_files = []
        if os.path.isdir(mf_dir):
            for f in os.listdir(mf_dir):
                fp = os.path.join(mf_dir, f)
                if os.path.isfile(fp) and is_mf_statement(fp):
                    mf_files.append(fp)

        if not mf_files:
            st.warning("No MF statement files found in data/statements/")
        else:
            st.write(f"Found {len(mf_files)} MF statement file(s)")

            # Load MF transactions (placeholder - would parse actual MF files)
            # For now show structure
            st.subheader("MF Portfolio Metrics")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total MF Value", "N/A")
            col2.metric("Total Invested", "N/A")
            col3.metric("MF XIRR", "N/A")
            col4.metric("Total Gains", "N/A")

    except Exception as e:
        st.error(f"MF overview unavailable: {e}")

# ============================================================
# MF Holdings Tab
# ============================================================
tab_mf_holdings = tabs[8]
with tab_mf_holdings:
    st.subheader("MF Scheme Holdings")
    st.info("Scheme-wise holdings requires MF statements with NAV data.")

    try:
        from mf.reports import scheme_holdings_report
        import pandas as pd

        st.write("MF holdings table will display here once MF statements are imported and NAVs are available.")
        st.empty()  # placeholder for holdings table

    except Exception as e:
        st.error(f"MF holdings unavailable: {e}")

# ============================================================
# MF XIRR Tab
# ============================================================
tab_mf_xirr = tabs[9]
with tab_mf_xirr:
    st.subheader("MF Scheme XIRR")
    st.info("Scheme-level XIRR requires MF transactions and NAV data.")

    try:
        from mf.reports import scheme_xirr_report
        st.write("MF XIRR report will display here once MF transactions are imported.")

    except Exception as e:
        st.error(f"MF XIRR unavailable: {e}")

# ============================================================
# MF Reconciliation Tab
# ============================================================
tab_mf_recon = tabs[10]
with tab_mf_recon:
    st.subheader("MF Reconciliation")
    st.info("Reconciles reconstructed units with reported holdings.")

    try:
        from mf.reports import reconciliation_report
        st.write("Reconciliation report will display here once MF data is available.")

    except Exception as e:
        st.error(f"MF reconciliation unavailable: {e}")

# ============================================================
# Consolidated Overview Tab
# ============================================================
tab_cons_overview = tabs[11]
with tab_cons_overview:
    st.subheader("Consolidated Portfolio Overview")
    st.info("Combined Equity + MF portfolio view.")

    try:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Portfolio Value", "N/A")
        col2.metric("Total Invested Capital", "N/A")
        col3.metric("Consolidated XIRR", "N/A")
        col4.metric("Total Dividend Income", "N/A")

        st.write("Consolidated view combines equity and MF holdings once both data sources are available.")

    except Exception as e:
        st.error(f"Consolidated overview unavailable: {e}")

# ============================================================
# Consolidated Allocation Tab
# ============================================================
tab_cons_alloc = tabs[12]
with tab_cons_alloc:
    st.subheader("Consolidated Asset Allocation")
    st.info("Equity vs Debt vs Hybrid vs Gold vs Other allocation.")

    try:
        import pandas as pd
        from mf.allocation import classify_scheme

        st.write("Allocation breakdown will display here once portfolio data is available.")
        st.empty()

    except Exception as e:
        st.error(f"Consolidated allocation unavailable: {e}")

# ============================================================
# Consolidated Performance Tab
# ============================================================
tab_cons_perf = tabs[13]
with tab_cons_perf:
    st.subheader("Consolidated Performance vs Benchmark")
    st.info("Combined portfolio performance vs Nifty 50 / Nifty 500 TRI.")

    try:
        st.write("Performance comparison charts will display here once equity + MF data is available.")
        st.empty()

    except Exception as e:
        st.error(f"Consolidated performance unavailable: {e}")
