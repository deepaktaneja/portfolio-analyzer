"""Page 5 — Data & Settings.

Equity/MF imports, reconciliation and data-quality reports, market price/NAV and
corporate-action management, MCP connections and application settings.
Consolidates the former Data Management, Market Data and MF Reconciliation tabs.
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from app.components import ui
from app.context import AppContext
from app.shared import security_name


def _fetch_missing_prices(start, end, market_db, security_ids, persist=True, progress=None,
                          provider=None):
    """Fetch prices for ``security_ids`` over ``[start, end]`` and show a summary.

    Thin UI wrapper around :func:`market_data.prices.fetch_stock_prices`, which
    resolves each security's Yahoo ticker from the symbol map, tries the
    requested provider chain and optionally stores the rows in the local
    market-data DB.
    """
    from market_data.prices import fetch_stock_prices

    rows = []
    targets = list(security_ids or [])
    total = len(targets)
    for i, sid in enumerate(targets, start=1):
        res = fetch_stock_prices(
            sid, start=start, end=end, db_path=market_db, persist=persist, provider=provider
        )
        rows.append({
            "security_id": sid,
            "symbol": res.get("symbol"),
            "provider": res.get("source"),
            "rows": res.get("rows_fetched", 0),
            "stored": res.get("upserted", 0),
            "status": "ok" if res.get("rows_fetched") else "no data",
            "detail": "; ".join(res.get("errors") or []),
        })
        if progress is not None:
            progress.progress(i / total if total else 1.0, text=f"{i}/{total} {sid}")

    fetched = sum(r["rows"] for r in rows)
    stored = sum(r["stored"] for r in rows)
    if fetched:
        st.success(f"Fetched {fetched} price rows ({stored} stored) for {total} securities.")
    else:
        st.warning("No prices returned - check the symbol mapping and the date range.")
    failed = [r for r in rows if r["status"] != "ok"]
    if failed:
        st.dataframe(pd.DataFrame(failed), hide_index=True, width="stretch")
    return rows


def _render_data_management(ctx: AppContext) -> None:
    txs, holdings, market_db = ctx.txs, ctx.holdings, ctx.market_db_path
    ui.section("Data Imports & Quality")
    from src.store import SQLiteStore
    store = SQLiteStore(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3"))
    st.write("Transactions loaded:", len(txs))
    if not txs:
        st.info("No transactions imported.")
    st.write("Holdings:", 0 if holdings is None else len(holdings))
    if holdings is None or getattr(holdings, "empty", True):
        st.info("No holdings statement imported.")

    with st.expander("Opening Positions", expanded=False):
        from ledger.opening import infer_minimum_opening
        inferred = infer_minimum_opening(txs)
        if inferred:
            st.warning("Missing opening holdings detected for some stocks.")
            st.dataframe(
                pd.DataFrame([{"Stock": k, "Minimum Opening Qty": v, "Status": "Provisional"} for k, v in inferred.items()]),
                hide_index=True, width="stretch",
            )
            if st.button("Infer opening positions (provisional)", key="infer_open"):
                for isin, qty in inferred.items():
                    store.add_opening_position(isin, "2018-01-01", qty, source="inferred", verification_status="inferred", person_id=ctx.filters.person_id)
                st.success("Inferred opening positions added (provisional).")
                st.rerun()
        with st.form("opening_form_manual"):
            isin_in = st.text_input("ISIN")
            qty_in = st.number_input("Opening Qty", min_value=0.0)
            date_in = st.date_input("Ledger Opening Date")
            if st.form_submit_button("Add Opening Position"):
                store.add_opening_position(isin_in, str(date_in), qty_in, source="manual", verification_status="verified", person_id=ctx.filters.person_id)
                st.success("Opening position added.")
                st.rerun()
        ops = store.get_opening_positions(person_id=ctx.filters.person_id)
        if ops:
            st.dataframe(pd.DataFrame(ops), hide_index=True, width="stretch")
        else:
            st.info("No opening positions recorded.")

    with st.expander("Holdings Reconciliation", expanded=False):
        from corporate_actions.models import CorporateAction
        from market_data.repository import MarketDataStore as _MDS

        def _conv(r):
            from ledger.holdings_service import _parse_date
            return CorporateAction(
                security_id=r["security_id"], event_type=r["event_type"],
                ex_date=_parse_date(r.get("ex_date")), effective_date=_parse_date(r.get("effective_date")),
                ratio=r.get("ratio"), successor_id=r.get("successor_id"), verified=bool(r.get("verified")),
                source=r.get("source"), verification_status=r.get("verification_status"),
            )

        _ca_objs = [_conv(r) for r in _MDS(market_db).get_corporate_actions()]
        openings = store.get_opening_positions(person_id=ctx.filters.person_id)
        from datetime import date as _date
        _as_of = ctx.filters.end_date or _date.today()
        if holdings is not None and getattr(holdings, "attrs", None):
            _as_of = holdings.attrs.get("as_of_date") or _as_of
        snap = {}
        if holdings is not None:
            for _, r in holdings.iterrows():
                isin = str(r.get("isin") or r.get("security") or "").strip().upper()
                if isin:
                    snap[isin] = float(r.get("quantity") or 0)
        from ledger.holdings_service import get_holdings_reconciliation
        rows = get_holdings_reconciliation(_as_of, transactions=txs, actions=_ca_objs, opening_positions=openings, snapshot=snap, db_path=market_db)
        if rows:
            _rdf = pd.DataFrame(rows)
            if "isin" in _rdf.columns:
                _rdf["security_name"] = _rdf["isin"].map(lambda x: security_name(x, txs, holdings))
            st.dataframe(_rdf, hide_index=True, width="stretch")
        else:
            st.success("Holdings reconcile.")
        try:
            from ledger.holdings_service import get_reverse_opening_candidates
            _cands = get_reverse_opening_candidates(_as_of, transactions=txs, snapshot=snap, actions=_ca_objs, opening_positions=openings)
            if _cands:
                st.subheader("Candidate opening positions (provisional)")
                st.dataframe(pd.DataFrame([{"isin": k, "candidate_opening_qty": v, "status": "provisional"} for k, v in _cands.items()]), hide_index=True, width="stretch")
                if st.button("Add candidate openings (provisional)", key="add_cand"):
                    for isin, qty in _cands.items():
                        store.add_opening_position(isin, str(_as_of), float(qty), source="reversed_from_xls", verification_status="provisional")
                    st.success("Candidate opening positions added (provisional).")
                    st.rerun()
        except Exception as e:  # noqa: BLE001
            st.warning(f"Reverse opening candidates unavailable: {e}")

    with st.expander("Data Quality Checks", expanded=False):
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

    with st.expander("Danger Zone", expanded=False):
        if st.button("Clear All Data", key="clear_all"):
            import shutil
            for db_path in ["data/xirr.sqlite3", "data/market_data.sqlite3", "data/portfolio.sqlite3"]:
                if os.path.exists(db_path):
                    shutil.rmtree(db_path)
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.success("All data cleared. Please reload.")
            st.rerun()


def _render_market_data(ctx: AppContext) -> None:
    txs, holdings, market_db = ctx.txs, ctx.holdings, ctx.market_db_path
    f = ctx.filters
    ui.section("Market Data Management")
    from market_data.repository import MarketDataStore
    md = MarketDataStore(market_db)
    _mdr = md.get_market_data_quality_report()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Securities with prices", len(_mdr.get("securities", [])))
    m2.metric("Anomalies", len(_mdr.get("anomalies", [])))
    from market_data.fetcher import discover_securities as _discover
    _discovered = _discover(db_path=market_db)
    _with_prices = set(_mdr.get("securities", []))
    _missing_prices = [s for s in _discovered if s not in _with_prices]
    m3.metric("Securities requiring data", len(_discovered))
    m4.metric("Securities missing prices", len(_missing_prices))
    if _missing_prices:
        st.warning("Missing prices for: " + ", ".join(_missing_prices))

    try:
        _cas = md.get_corporate_actions()
        _unverified = [a for a in _cas if not a.get("verified")]
        st.metric("Corporate actions requiring verification", len(_unverified))
        if _unverified:
            st.dataframe(pd.DataFrame(_unverified), hide_index=True, width="stretch")
    except Exception as e:  # noqa: BLE001
        st.error(f"Corporate actions unavailable: {e}")
    try:
        _divs = md.get_dividend_events() if hasattr(md, "get_dividend_events") else []
        st.metric("Dividend records", len(_divs))
        if not _divs:
            st.info("No dividend records available.")
    except Exception as e:  # noqa: BLE001
        st.error(f"Dividend records unavailable: {e}")
    from market_data.prices import get_index_tri as _tri
    for _iid in ("NIFTY50TRI", "NIFTY500TRI"):
        try:
            _v = _tri(_iid, str(f.end_date), db_path=market_db)
            st.write(f"{_iid}: {'available' if _v is not None else 'missing'}")
        except Exception as e:  # noqa: BLE001
            st.warning(f"{_iid} unavailable: {e}")
    runs = md.get_sync_runs(limit=1)
    if runs:
        ui.data_freshness_caption("Last sync", f"{runs[0].get('started_at')} ({runs[0].get('status')})")

    from market_data.prices import (
        get_market_data_quality_report,
        get_sync_runs,
        sync_all_market_data,
    )
    try:
        report = get_market_data_quality_report(db_path=market_db)
        st.write("Discovered securities:", report.get("securities", []))
        st.dataframe(pd.DataFrame([{"security_id": k, "security_name": security_name(k, txs, holdings), **v} for k, v in report.get("stock_coverage", {}).items()]), hide_index=True, width="stretch")
    except Exception as e:  # noqa: BLE001
        st.error(f"Market data report unavailable: {e}")
    try:
        st.dataframe(pd.DataFrame(get_sync_runs(db_path=market_db)), hide_index=True, width="stretch")
    except Exception as e:  # noqa: BLE001
        st.error(f"Sync runs unavailable: {e}")

    c1, c2 = st.columns(2)
    if c1.button("Sync all market data", key="sync_all"):
        res = sync_all_market_data(db_path=market_db, provider="manual", security_ids=_discovered or None)
        st.json(res)
        st.rerun()
    c2.caption(
        "Local sync reads CSV files only. Use 'Fetch Historical Prices' below to "
        "backfill gaps from Yahoo Finance."
    )

    with st.expander("Fetch Historical Prices (Yahoo Finance)", expanded=False):
        st.caption(
            "Backfill prices the local market-data DB does not cover (for example a "
            "2014-2019 XIRR period). Every security needs a symbol mapping."
        )
        from datetime import date as _d
        from datetime import timedelta as _td

        from market_data.prices import price_fetch_enabled

        if not price_fetch_enabled():
            st.info(
                "On-demand fetching is disabled (ICICI_PRICE_FETCH_FALLBACK=false); "
                "this manual fetch still works."
            )
        _tx_days = sorted(
            str(t.get("trade_date") or "")[:10] for t in txs if t.get("trade_date")
        )
        try:
            _default_from = _d.fromisoformat(_tx_days[0]) if _tx_days else _d.today() - _td(days=3650)
        except ValueError:
            _default_from = _d.today() - _td(days=3650)
        hx1, hx2 = st.columns(2)
        hist_start = hx1.date_input("From", value=_default_from, key="hist_price_start")
        hist_end = hx2.date_input("To", value=_d.today(), key="hist_price_end")
        _secs = list(_discovered or [])
        hist_sids = st.multiselect(
            "Securities",
            _secs,
            default=_secs,
            key="hist_price_sids",
            help="Securities without a symbol mapping cannot be fetched.",
        )
        hist_persist = st.checkbox(
            "Store fetched prices in the local DB", value=True, key="hist_price_persist"
        )
        hist_provider = st.selectbox(
            "Provider",
            ["auto", "yfinance", "yahoo_chart"],
            key="hist_price_provider",
            help=(
                "auto tries the yfinance library, then the Yahoo chart HTTP endpoint. "
                "Overrides ICICI_PRICE_PROVIDER for this fetch."
            ),
        )
        try:
            _mapped = {
                str(r.get("security_id") or "").strip().upper()
                for r in md.get_security_symbol_map()
            }
        except Exception:  # noqa: BLE001
            _mapped = set()
        _unmapped = [s for s in hist_sids if s not in _mapped]
        if _unmapped:
            _extra = f" (+{len(_unmapped) - 10} more)" if len(_unmapped) > 10 else ""
            st.warning("No symbol mapping for: " + ", ".join(_unmapped[:10]) + _extra)
        if st.button("Fetch prices", key="hist_price_fetch"):
            if not hist_sids:
                st.warning("Select at least one security.")
            elif hist_start > hist_end:
                st.warning("'From' must be on or before 'To'.")
            else:
                _bar = st.progress(0.0, text="Starting...")
                _fetch_missing_prices(
                    str(hist_start),
                    str(hist_end),
                    market_db,
                    hist_sids,
                    persist=hist_persist,
                    progress=_bar,
                    provider=hist_provider,
                )
                _bar.empty()

    with st.expander("Symbol Mapping", expanded=False):  # noqa: SIM117
        with st.form("symbol_map_form"):
            sid = st.text_input("Security ID")
            sym = st.text_input("Provider symbol")
            prov = st.text_input("Provider", value="yfinance")
            if st.form_submit_button("Save mapping"):
                md.upsert_security_symbol_map([{"security_id": sid, "symbol": sym, "provider_symbol": sym, "provider": prov}])

    with st.expander("Corporate Actions", expanded=False):
        try:
            st.dataframe(pd.DataFrame(md.get_corporate_actions()), hide_index=True, width="stretch")
        except Exception as e:  # noqa: BLE001
            st.error(f"Corporate actions unavailable: {e}")
        with st.form("verify_ca_form"):
            ca_id = st.number_input("Corporate action ID", min_value=1, step=1)
            if st.form_submit_button("Verify"):
                md.verify_corporate_action(int(ca_id), verified=True, verified_by="manual")
                st.success("Verified")
                st.rerun()
        st.markdown("##### Manual corporate action entry")
        with st.form("manual_ca_form"):
            sid = st.text_input("Security ID")
            et = st.selectbox("Event type", ["split", "bonus", "stock_dividend", "rights", "merger", "demerger", "buyback"])
            exd = st.date_input("Ex date")
            ratio = st.number_input("Ratio", value=1.0)
            succ = st.text_input("Successor ID (merger/demerger)")
            alloc = st.number_input("Cost allocation (merger/demerger)", value=1.0)
            if st.form_submit_button("Add"):
                md.upsert_corporate_actions([{"security_id": sid, "event_type": et, "ex_date": str(exd), "ratio": ratio, "successor_id": succ or None, "cost_allocation": alloc if et in ("merger", "demerger") else None, "verified": True, "verification_status": "manual", "source": "manual"}])
                st.success("Added")
                st.rerun()

    with st.expander("Benchmark (Nifty TRI) & Dividend Data", expanded=False):
        with st.form("tri_sync_form"):
            tri_index_sync = st.selectbox("Index to sync", ["NIFTY50TRI", "NIFTY500TRI"], key="tri_sync_idx")
            tri_start = st.date_input("TRI start date", key="tri_sync_start")
            tri_end = st.date_input("TRI end date", key="tri_sync_end")
            tri_url = st.text_input("Optional CSV/HTML URL (blank = Niftyindices endpoint)")
            if st.form_submit_button("Sync TRI"):
                try:
                    from market_data.fetcher import sync_index_tri
                    res = sync_index_tri(tri_index_sync, start=str(tri_start), end=str(tri_end), db_path=market_db, source_url=tri_url or None)
                    st.success(f"Synced {res.get('upserted', 0)} TRI rows")
                    if res.get("warnings"):
                        st.warning("; ".join(res["warnings"][:10]))
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"TRI sync failed: {e}")
        with st.form("dividend_import_form"):
            div_path = st.text_input("Dividend CSV path")
            if st.form_submit_button("Import dividends"):
                try:
                    from market_data.fetcher import sync_dividend_events
                    res = sync_dividend_events(div_path, db_path=market_db)
                    st.success(f"Imported {res.get('upserted', 0)} dividend records")
                    if res.get("warnings"):
                        st.warning("; ".join(res["warnings"][:10]))
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Dividend import failed: {e}")
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


def _render_mf_data(ctx: AppContext) -> None:
    with st.expander("Mutual Fund Data Import & NAV Sync", expanded=False):
        st.caption("Import MF Central statements and AMFI/AMC holdings disclosures; sync NAVs from AMFI.")
        from pathlib import Path

        mf_dir = Path("data/mf")
        mf_dir.mkdir(parents=True, exist_ok=True)

        # Person tagging: route uploads to data/mf/<slug> when a person is chosen
        # so the sidebar Person filter (get_mf_context) loads the right folder.
        _people: list[str] = []
        try:
            from app.context import _xirr_db
            from src.people import PersonStore

            _people = [str(p["name"]) for p in PersonStore(_xirr_db()).list_people(active_only=True)]
        except Exception:  # noqa: BLE001
            _people = []
        _opts = ["All people"] + _people
        _cur_person = getattr(ctx.filters, "person_name", None)
        _def_idx = _opts.index(_cur_person) if _cur_person in _opts else 0
        _upload_person = st.selectbox(
            "Tag uploads to person", _opts, index=_def_idx, key="mf_upload_person",
            help="Choose a person to store MF statements under data/mf/<person>. "
                 "Use the sidebar Person filter to view that person's portfolio.",
        )
        if _upload_person != "All people":
            target_dir = mf_dir / _person_slug(_upload_person)
        else:
            target_dir = mf_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        _where = str(target_dir) + ("" if _upload_person == "All people"
                                    else f" (shown when sidebar Person = {_upload_person})")
        st.caption("Target folder: `" + _where + "`")

        # 1) Upload MF TRANSACTION statement files
        uploads = st.file_uploader(
            "Upload MF transaction statement(s) (CSV/XLSX/XLS)",
            type=["csv", "xlsx", "xls"],
            accept_multiple_files=True,
            key="mf_upload",
        )
        if uploads and st.button("Save uploaded MF statements", key="mf_save_uploads"):
            import pandas as pd

            from mf.importers import parse_mf_statement
            from mf.service import _load_transactions_from_folder, partition_new_mf_transactions

            _n_saved = 0
            _tx_total = 0
            _new_tx_total = 0
            _skipped_tx_total = 0
            _mf_duplicates: list[dict] = []
            _codes: set[str] = set()
            _dates: list[str] = []
            _warns: list[str] = []
            for uf in uploads:
                _existing_txs = _load_transactions_from_folder(str(target_dir))
                dest = target_dir / uf.name
                dest.write_bytes(uf.getbuffer())
                _n_saved += 1
                try:
                    _rows, _w = parse_mf_statement(str(dest))
                except Exception as e:  # noqa: BLE001
                    _rows, _w = [], [str(e)]
                _new_rows, _duplicate_rows = partition_new_mf_transactions(_rows, _existing_txs)
                _tx_total += len(_rows)
                _new_tx_total += len(_new_rows)
                _skipped_tx_total += len(_duplicate_rows)
                _mf_duplicates.extend(_duplicate_rows)
                _codes.update(str(r.get("scheme_code")) for r in _new_rows if r.get("scheme_code"))
                _dates.extend(str(r.get("trade_date") or "")[:10] for r in _new_rows if r.get("trade_date"))
                _warns.extend(_w[:5])
            _clear_data_caches()
            if _tx_total:
                _span = f"{min(_dates)} to {max(_dates)}" if _dates else "n/a"
                _mf_msg = (
                    f"✅ Uploaded {_new_tx_total} new MF transactions | "
                    f"⚠️ Skipped {_skipped_tx_total} duplicate MF transactions."
                )
                if _skipped_tx_total:
                    st.warning(_mf_msg)
                else:
                    st.success(_mf_msg)
                if _upload_person != "All people":
                    st.info(f"Tip: set the sidebar **Person** filter to '{_upload_person}' to view this portfolio.")
            else:
                st.warning(
                    f"Saved {_n_saved} file(s) to {target_dir}, but parsed 0 transactions — "
                    "confirm this is a transaction statement (date/type/units columns)."
                )
            if _mf_duplicates:
                st.warning("Duplicate MF transactions skipped:")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Date": str(r.get("trade_date") or r.get("date") or "")[:10],
                                "Scheme Name": str(r.get("scheme_name") or r.get("scheme_code") or ""),
                                "ISIN": str(r.get("scheme_code") or r.get("isin") or r.get("scheme_name") or ""),
                                "Folio": str(r.get("folio") or ""),
                                "Type": str(r.get("side") or ""),
                                "Units": float(r.get("units") or 0),
                                "NAV": float(r.get("nav") or 0),
                                "Amount": float(r.get("amount") or 0),
                            }
                            for r in _mf_duplicates
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )
            for _w in _warns[:5]:
                st.caption("• " + str(_w))

        # 1b) Upload CURRENT VALUATION (holding) statement files
        st.markdown("##### Current valuation / holding statements")
        val_ups = st.file_uploader(
            "Upload current valuation statement(s) (XLS/XLSX/CSV)",
            type=["xls", "xlsx", "csv"],
            accept_multiple_files=True,
            key="mf_val_upload",
            help="AMC portfolio valuation exports (scheme, units, NAV date, current value, cost value). "
                 "Seeds current NAVs and shows authoritative holdings.",
        )
        if val_ups and st.button("Import current valuation statements", key="mf_import_val"):
            try:
                import pandas as pd

                from mf.data import MFDataService
                from mf.importers import parse_current_valuation, parse_mf_statement

                val_dir = target_dir / "valuation"
                val_dir.mkdir(parents=True, exist_ok=True)

                # Existing transaction scheme codes, to bridge AMC product codes
                # (B292G / HAFWGT / FTI104) to valuation codes (292G / AFWGT / 104).
                tx_codes: set[str] = set()
                for f in sorted(target_dir.rglob("*")):
                    if not f.is_file() or f.suffix.lower() not in (".csv", ".xlsx", ".xls"):
                        continue
                    try:
                        rws, _w = parse_mf_statement(str(f))
                    except Exception:  # noqa: BLE001
                        rws = []
                    for rr in rws:
                        if rr.get("scheme_code"):
                            tx_codes.add(str(rr["scheme_code"]).strip().upper())

                svc = MFDataService()
                preview: list[dict] = []
                seeded = 0
                for uf in val_ups:
                    (val_dir / uf.name).write_bytes(uf.getbuffer())
                    recs = parse_current_valuation(val_dir / uf.name)
                    if not recs:
                        st.warning(f"{uf.name}: no valuation rows detected (check header/columns).")
                        continue
                    for rec in recs:
                        code = str(rec["scheme_code"]).strip()
                        cu = code.upper()
                        targets = {code}
                        for tc in tx_codes:
                            if tc == cu or tc.endswith(cu) or cu.endswith(tc):
                                targets.add(tc)
                        nav = rec.get("nav")
                        navd = rec.get("nav_date")
                        if nav and nav > 0 and navd:
                            for t in targets:
                                seeded += svc.store_nav_records(t, [(str(navd)[:10], float(nav))])
                        preview.append(rec)
                _clear_data_caches()
                if preview:
                    pdf = pd.DataFrame(preview)
                    show = [c for c in ["scheme_code", "scheme_name", "units", "nav", "nav_date", "current_value", "cost_value"] if c in pdf.columns]
                    st.dataframe(pdf[show], hide_index=True, width="stretch")
                    _cv = sum(float(r.get("current_value") or 0) for r in preview)
                    _cc = sum(float(r.get("cost_value") or 0) for r in preview)
                    _c1, _c2, _c3 = st.columns(3)
                    _c1.metric("Schemes", len(preview))
                    _c2.metric("Current value", f"Rs {_cv:,.0f}")
                    _c3.metric("Gain vs cost", f"Rs {_cv - _cc:,.0f}")
                st.success(f"Parsed {len(preview)} scheme row(s); seeded {seeded} NAV entr(ies).")
            except Exception as e:  # noqa: BLE001
                st.error(f"Valuation import failed: {e}")

        # 2) Import holdings / metadata CSVs (paths)
        c1, c2 = st.columns(2)
        with c1:
            hold_path = st.text_input("Scheme holdings CSV path", value=str(mf_dir / "holdings.csv"), key="mf_hold_path")
            if st.button("Import holdings disclosures", key="mf_import_holdings"):
                try:
                    from mf.data import load_scheme_holdings_csv, save_scheme_holdings
                    recs = load_scheme_holdings_csv(hold_path)
                    n = save_scheme_holdings(recs)
                    st.session_state.pop("mf_context", None)
                    st.success(f"Imported {n} holding rows")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Holdings import failed: {e}")
        with c2:
            meta_path = st.text_input("Fund metadata CSV path", value=str(mf_dir / "metadata.csv"), key="mf_meta_path")
            if st.button("Import fund metadata", key="mf_import_meta"):
                try:
                    from mf.data import load_fund_metadata_csv, save_fund_metadata
                    recs = load_fund_metadata_csv(meta_path)
                    n = save_fund_metadata(recs)
                    st.session_state.pop("mf_context", None)
                    st.success(f"Imported {n} metadata rows")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Metadata import failed: {e}")

        # 3) NAV sync (AMFI / mftool)
        st.markdown("##### Sync NAVs from AMFI (fetch)")
        from mf.service import load_mf_context
        try:
            preview = load_mf_context(folder=str(target_dir), fetch_nav=False)
            codes = preview.get("scheme_codes", [])
        except Exception:  # noqa: BLE001
            codes = []
        if codes:
            st.write("Detected schemes:", ", ".join(codes))
        if st.button("Sync NAVs for detected schemes", key="mf_sync_nav"):
            try:
                from mf.data import MFDataService
                svc = MFDataService(provider="mfapi")
                synced = 0
                for code in codes:
                    recs, status = svc.get_nav_history(code, use_cache=False)
                    if status == "fetched" and recs:
                        synced += 1
                _clear_data_caches()
                if synced:
                    st.success(f"Synced NAV history for {synced} scheme(s).")
                else:
                    st.warning("No NAVs fetched (network unavailable or codes invalid). Data remains cache-only.")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"NAV sync failed: {e}")
        else:
            st.info("No schemes detected yet — upload statements or import metadata/holdings first.")

        if st.button("Clear cached MF analytics", key="mf_clear_cache"):
            _clear_data_caches()
            st.success("MF analytics cache cleared.")
            st.rerun()


def _render_mf_reconciliation() -> None:
    with st.expander("Mutual Fund Reconciliation", expanded=False):
        st.caption("Reconciles reconstructed units with reported holdings.")
        try:
            from mf.reports import reconciliation_report  # noqa: F401
            st.write("Reconciliation report will display here once MF data is available.")
        except Exception as e:  # noqa: BLE001
            st.error(f"MF reconciliation unavailable: {e}")


def _render_decision_settings() -> None:
    with st.expander("Decision Engine — Thresholds & Signal History", expanded=False):
        st.caption("Stage 8 signals run in SHADOW mode (advisory only). Thresholds are configurable "
                   "starting assumptions, not automatic sell rules — edit config/decision_thresholds.yaml.")
        try:
            from decision.config import load_thresholds
            cfg = load_thresholds()
            st.markdown("##### Engine settings")
            st.json(cfg.get("engine", {}))
            st.markdown("##### Equity thresholds")
            st.json(cfg.get("equity", {}))
            st.markdown("##### Mutual-fund thresholds")
            st.json(cfg.get("mutual_fund", {}))
        except Exception as e:  # noqa: BLE001
            st.error(f"Thresholds unavailable: {e}")
        if st.button("Clear decision signal cache", key="clear_decision_cache"):
            st.session_state.pop("decision_recs", None)
            st.success("Decision signal cache cleared.")


def _render_rebalancing_settings() -> None:
    with st.expander("Rebalancing & Allocation Targets", expanded=False):
        st.caption("Stage 9 targets and tax assumptions are configurable starting points — edit "
                   "config/allocation_targets.yaml. Rebalancing is advisory only (no trades executed).")
        try:
            from portfolio.config import load_targets
            from portfolio.tax import get_tax_rules
            t = load_targets()
            st.markdown("##### Asset-class targets (%)")
            st.dataframe(pd.DataFrame([
                {"asset_class": cls, "target": cfg.get("target"), "min": cfg.get("min"), "max": cfg.get("max")}
                for cls, cfg in t.get("asset_classes", {}).items()
            ]), hide_index=True, width="stretch")
            st.markdown("##### Rebalancing strategy")
            st.json(t.get("rebalancing", {}))
            st.markdown("##### Sector caps (%)")
            st.json(t.get("sector_caps", {}))
            st.markdown("##### Applicable tax rules (as of today)")
            st.json(get_tax_rules(None, t.get("tax")))
        except Exception as e:  # noqa: BLE001
            st.error(f"Rebalancing settings unavailable: {e}")


def _render_automation_settings() -> None:
    with st.expander("Automation, Monitoring & Backups", expanded=False):
        st.caption("Stage 10 — scheduled refresh, MCP health, metrics and encrypted backups. "
                   "Advisory only; no trades and no external notifications are sent.")
        # ---- Scheduler status
        try:
            from scheduler.store import JobStore
            jobs = JobStore().list_jobs()
            st.markdown("##### Scheduler jobs")
            if jobs:
                st.dataframe(pd.DataFrame(jobs), hide_index=True, width="stretch")
            else:
                st.caption("No jobs registered yet — click 'Run due jobs' to register & run.")
        except Exception as e:  # noqa: BLE001
            st.caption(f"Scheduler status unavailable: {e}")

        # ---- Actions
        c1, c2, c3 = st.columns(3)
        if c1.button("Run due jobs", key="auto_run_due"):
            try:
                from scheduler.runner import Scheduler
                st.session_state["auto_last_run"] = Scheduler().run_due()
                st.success("Scheduler run complete.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Scheduler run failed: {e}")
        if c2.button("Health-check MCP", key="auto_health"):
            try:
                from mcp_integration.client import MCPClientManager
                from mcp_integration.health import run_health_checks
                st.session_state["auto_health"] = run_health_checks(MCPClientManager())
            except Exception as e:  # noqa: BLE001
                st.error(f"Health check failed: {e}")
        if c3.button("Evaluate alerts", key="auto_alerts"):
            try:
                from alerts.store import AlertStore
                from alerts.triggers import build_alerts
                generated = build_alerts(decision_recs=st.session_state.get("decision_recs"))
                kept = AlertStore().record_many(generated)
                st.success(f"Evaluated {len(generated)} alert(s); stored {len(kept)} new.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Alert evaluation failed: {e}")

        last = st.session_state.get("auto_last_run")
        if last:
            st.markdown("##### Last scheduler run")
            st.dataframe(pd.DataFrame([{"job": r.get("job"), "status": r.get("status"),
                                        "rows": r.get("rows", 0)} for r in last]),
                         hide_index=True, width="stretch")
        health = st.session_state.get("auto_health")
        if health:
            st.markdown("##### MCP health")
            st.dataframe(pd.DataFrame(health), hide_index=True, width="stretch")

        # ---- Metrics
        if st.button("Refresh metrics", key="auto_metrics"):
            try:
                from monitoring.metrics import collect_metrics
                st.session_state["auto_metrics"] = collect_metrics(st.session_state.get("auto_health"))
            except Exception as e:  # noqa: BLE001
                st.error(f"Metrics collection failed: {e}")
        m = st.session_state.get("auto_metrics")
        if m:
            st.markdown("##### Metrics")
            st.json({
                "scheduler_failed_runs": (m.get("scheduler") or {}).get("failed_runs"),
                "alerts": m.get("alerts"),
                "llm_usage": m.get("llm_usage"),
                "mcp_cache": m.get("mcp_cache"),
                "databases_bytes": m.get("databases"),
            })
            try:
                from monitoring.metrics import to_prometheus_text
                st.download_button("Export metrics (Prometheus)", to_prometheus_text(m),
                                   file_name="metrics.prom",
                                   mime="text/plain", key="auto_prom")
            except Exception:  # noqa: BLE001, S110
                pass

        # ---- Encrypted backups
        st.markdown("##### Encrypted backups")
        st.caption("Set BACKUP_ENCRYPTION_KEY in .env to enable. Backups are Fernet-encrypted "
                   "with sha256 verification; restore via scripts/restore.py.")
        if st.button("Back up databases now", key="auto_backup"):
            try:
                from scheduler.jobs import job_backup_databases
                res = job_backup_databases({})
                if res.get("status") == "success":
                    st.success(f"Backed up {res.get('rows', 0)} database(s).")
                else:
                    st.warning(res.get("details", {}).get("reason") or res.get("details", {}).get("error"))
            except Exception as e:  # noqa: BLE001
                st.error(f"Backup failed: {e}")
        try:
            import glob
            backups = sorted(glob.glob("data/backups/*.db.enc"))[-10:]
            if backups:
                st.dataframe(pd.DataFrame([{"backup": b} for b in backups]),
                             hide_index=True, width="stretch")
        except Exception:  # noqa: BLE001, S110
            pass


def _render_settings() -> None:
    with st.expander("MCP Connections & Application Settings", expanded=False):
        try:
            from config.settings import get_settings, load_mcp_registry
            s = get_settings()
            st.markdown("##### MCP Servers (own registry)")
            reg = load_mcp_registry()
            st.dataframe(
                pd.DataFrame([
                    {"name": r.name, "transport": r.transport, "enabled": r.enabled, "max_concurrency": r.max_concurrency}
                    for r in reg
                ]),
                hide_index=True, width="stretch",
            )
            st.markdown("##### LLM Providers")
            st.write({
                "gemini_configured": bool(s.gemini_api_key),
                "openrouter_configured": bool(s.openrouter_api_key),
                "temperature": s.llm_temperature,
                "max_tokens": s.llm_max_tokens,
            })
            st.caption("Credentials are read from .env (never displayed). Configure GEMINI_API_KEY / OPENROUTER_API_KEY.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Settings unavailable: {e}")


def render(ctx: AppContext) -> None:
    st.markdown("### Data & Settings")
    st.caption("Imports, reconciliation, market data, MCP connections and application settings.")
    _render_data_management(ctx)
    st.divider()
    _render_people(ctx)
    st.divider()
    _render_market_data(ctx)
    st.divider()
    _render_mf_data(ctx)
    _render_mf_reconciliation()
    _render_decision_settings()
    _render_rebalancing_settings()
    _render_automation_settings()
    _render_settings()




def _person_slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "person"


def _clear_data_caches() -> None:
    for k in ("all_txs", "ctx_cache"):
        st.session_state.pop(k, None)
    for k in list(st.session_state.keys()):
        if str(k).startswith("mf_context"):
            st.session_state.pop(k, None)


def _render_people(ctx: AppContext) -> None:
    import os
    from datetime import date
    from pathlib import Path

    import pandas as pd

    st.markdown("### People & Portfolios (Multi-Person)")
    st.caption("Tag statements to a person, then use the sidebar Person filter on any page.")
    try:
        from app.context import _xirr_db
        from src.people import PersonStore
        store = PersonStore(_xirr_db())
    except Exception as e:  # noqa: BLE001
        st.error("People store unavailable: " + str(e))
        return

    people = store.list_people(active_only=False)
    counts = store.person_transaction_counts()

    with st.expander("Manage people", expanded=False):
        if people:
            rows = [{"id": p["id"], "name": p["name"], "active": bool(p["active"]), "transactions": counts.get(int(p["id"]), 0)} for p in people]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.info("No people yet. Add one below.")
        c1, c2 = st.columns(2)
        with c1:
            new_name = st.text_input("Add person", key="people_new_name", placeholder="e.g. Anmol")
            if st.button("Add person", key="people_add"):
                try:
                    pid_new = store.create_person(new_name)
                    _clear_data_caches()
                    st.success("Added person #" + str(pid_new) + ": " + new_name.strip())
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error("Could not add: " + str(e))
        with c2:
            if people:
                sel = st.selectbox("Select person", [p["name"] for p in people], key="people_sel")
                rec = next((p for p in people if p["name"] == sel), None)
                if rec:
                    rename_to = st.text_input("Rename to", value=rec["name"], key="people_rename_to")
                    b1, b2 = st.columns(2)
                    if b1.button("Rename", key="people_rename"):
                        try:
                            store.rename_person(int(rec["id"]), rename_to)
                            _clear_data_caches()
                            st.success("Renamed.")
                            st.rerun()
                        except Exception as e:  # noqa: BLE001
                            st.error("Rename failed: " + str(e))
                    if b2.button("Archive" if rec["active"] else "Restore", key="people_toggle"):
                        try:
                            store.set_person_active(int(rec["id"]), not bool(rec["active"]))
                            _clear_data_caches()
                            st.rerun()
                        except Exception as e:  # noqa: BLE001
                            st.error("Update failed: " + str(e))

    active = [p for p in people if p["active"]]
    if not active:
        st.info("Add a person first, then import statements tagged to them.")
        return
    target = st.selectbox("Import statements for", [p["name"] for p in active], key="people_import_target")
    trec = next((p for p in people if p["name"] == target), None)
    if trec is None:
        return
    pid = int(trec["id"])
    slug = _person_slug(trec["name"])
    person_label = str(trec.get("name"))

    def _row_key(r):
        """Security identifier: ISIN when present, else the security name."""
        return str(r.get("isin") or r.get("security") or "").strip().upper()

    def _is_clean_row(r):
        """Accept name-only rows (no ISIN column) if they carry a security key."""
        issues = set(r.get("issues") or [])
        blocking = issues - {"invalid_isin"}
        return not blocking and bool(_row_key(r))

    def _norm_name(s):
        import re as _re
        return _re.sub(r"[^a-z0-9]", "", str(s or "").lower())

    def _held_context(parsed_rows, keys_replaced):
        """Identify securities that belong to CURRENT holdings (to be excluded).

        A security is "currently held" when ANY of:
          * it appears in the latest holdings snapshot (matched by ISIN), or
          * its full-history net position across stored + new rows is > 0, or
          * its name matches a held symbol/name (name-only statements lack ISIN).
        Returns (held_keys, held_isins, held_names).
        """
        pos = {}
        for t in list(store.all_transactions(pid)) + list(parsed_rows):
            if t.get("source_file") in keys_replaced:
                continue
            k = _row_key(t)
            if not k:
                continue
            try:
                q = float(t.get("quantity") or 0.0)
            except (TypeError, ValueError):
                q = 0.0
            pos[k] = pos.get(k, 0.0) + (q if str(t.get("buy_sell") or "").upper() == "BUY" else -q)
        held_keys = {k for k, v in pos.items() if v > 1e-6}
        held_isins, held_names = set(), set()
        try:
            for h in store.get_latest_holdings(pid) or []:
                try:
                    qty = float(h.get("quantity") or 0)
                except (TypeError, ValueError):
                    qty = 0.0
                if qty <= 0:
                    continue
                isin = str(h.get("isin") or "").strip().upper()
                if isin:
                    held_isins.add(isin)
                for nm in (h.get("symbol"), h.get("security")):
                    nn = _norm_name(nm)
                    if len(nn) >= 4:
                        held_names.add(nn)
        except Exception:  # noqa: BLE001, S110
            pass
        return held_keys, held_isins, held_names

    def _is_held(r, held_keys, held_isins, held_names):
        k = _row_key(r)
        if k in held_keys or k in held_isins:
            return True
        name = _norm_name(r.get("security"))
        for s in held_names:
            if s and name and (s in name or name in s) and min(len(s), len(name)) >= 4:
                return True
        return False

    def _render_import_summary():
        s = st.session_state.get("_import_summary")
        if not s:
            return
        # --- Clear post-upload receipt ---
        person = str(s.get("person", ""))
        files = s.get("files") or []
        total_parsed = sum(int(f.get("Parsed") or 0) for f in files)
        total_imported = int(s.get("imported", 0))
        total_skipped = int(s.get("skipped", 0))
        date_froms = [str(f.get("DateFrom") or "") for f in files if f.get("DateFrom")]
        date_tos = [str(f.get("DateTo") or "") for f in files if f.get("DateTo")]
        date_range = ""
        if date_froms and date_tos:
            date_range = min(date_froms) + " → " + max(date_tos)
        file_list = ", ".join(str(f.get("File", "")) for f in files) or "(no files)"
        all_ok = all(str(f.get("Status", "")).startswith("✅") for f in files) and files
        receipt = (
            "📥 **Import receipt** — Portfolio: **" + person + "** | "
            "File: " + file_list + " | "
            "Parsed: " + str(total_parsed) + " | "
            "New trades added: " + str(total_imported) + " | "
            "Duplicates skipped: " + str(total_skipped)
        )
        if date_range:
            receipt += " | Trade dates: " + date_range
        if all_ok:
            st.success(receipt, icon="✅")
        elif total_imported > 0:
            st.info(receipt, icon="ℹ️")
        else:
            st.warning(receipt, icon="⚠️")
        # --- Existing detail sections ---
        duplicates = s.get("duplicates") or []
        if duplicates:
            st.warning("Duplicate transactions skipped:")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Date": str(r.get("trade_date") or r.get("settlement_date") or "")[:10],
                            "Stock Code": str(r.get("security") or r.get("isin") or ""),
                            "ISIN": str(r.get("isin") or ""),
                            "Type": str(r.get("buy_sell") or ""),
                            "Quantity": float(r.get("quantity") or 0),
                            "Price": float(r.get("gross_rate") or r.get("price") or 0),
                        }
                        for r in duplicates
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        if s.get("filter_note"):
            st.caption(s["filter_note"])
        st.dataframe(pd.DataFrame(files), hide_index=True, width="stretch")
        st.caption("Portfolio XIRR is recomputed from these transactions on the Performance and Portfolio pages.")
        if st.button("Dismiss upload summary", key="dismiss_import_summary"):
            st.session_state.pop("_import_summary", None)
            st.rerun()

    def _import_statement_files(uploads, folder, parser, only_closed, cache_prefix, source_kind="PDF"):
        if not uploads:
            st.warning("No files selected for upload.")
            return
        n = len(uploads)
        progress = st.progress(0.0, text="Starting " + source_kind + " import…")
        reports, parsed = [], []
        for i, uf in enumerate(uploads):
            progress.progress(i / n, text="Parsing " + uf.name + " (" + str(i + 1) + "/" + str(n) + ")…")
            dest = folder / uf.name
            with open(dest, "wb") as fh:
                fh.write(uf.getbuffer())
            rep = {"File": uf.name, "Status": "⏳", "Parsed": 0, "Imported": 0, "Note": ""}
            reports.append(rep)
            try:
                all_rows = parser(str(dest))
            except Exception as e:  # noqa: BLE001
                rep["Status"] = "❌ Error"
                rep["Note"] = str(e)
                continue
            if not all_rows:
                rep["Status"] = "⏭️ Skipped"
                rep["Note"] = "No transactions found (scanned/unsupported statement)"
                continue
            clean = [r for r in all_rows if _is_clean_row(r)]
            bad = len(all_rows) - len(clean)
            rep["Status"] = "🔄 Parsed"
            rep["Parsed"] = len(clean)
            if bad:
                rep["Note"] = str(bad) + " invalid row(s) skipped"
            parsed.append({"key": cache_prefix + uf.name, "rows": clean, "dest": dest, "rep": rep})

        keys_replaced = {p["key"] for p in parsed}
        all_new = [r for p in parsed for r in p["rows"]]
        held_keys, held_isins, held_names = (set(), set(), set())
        if only_closed and all_new:
            held_keys, held_isins, held_names = _held_context(all_new, keys_replaced)

        progress.progress(0.95, text="Saving to database…")
        imported, filtered_out, companies = 0, 0, set()
        excluded_names = set()
        duplicates: list[dict] = []
        skipped = 0
        for p in parsed:
            rows = p["rows"]
            new_rows, duplicate_rows = store.partition_new_transactions(
                rows, person_id=pid, exclude_source_file=p["key"]
            )
            skipped += len(duplicate_rows)
            duplicates.extend(duplicate_rows)
            if only_closed:
                kept = []
                for r in new_rows:
                    if _is_held(r, held_keys, held_isins, held_names):
                        excluded_names.add(_row_key(r))
                    else:
                        kept.append(r)
                filtered_out += len(new_rows) - len(kept)
                new_rows = kept
            store.replace_file_transactions(p["key"], new_rows, person_id=pid)
            try:
                store.upsert_file(p["key"], os.path.getmtime(p["dest"]), len(new_rows))
            except Exception:  # noqa: BLE001, S110
                pass
            imported += len(new_rows)
            for r in new_rows:
                k = _row_key(r)
                if k:
                    companies.add(k)
            # Trade date range for this file's newly imported rows.
            tx_dates = [str(r.get("trade_date") or r.get("settlement_date") or "") for r in new_rows if r.get("trade_date") or r.get("settlement_date")]
            tx_dates = [d[:10] for d in tx_dates if d]
            p["rep"]["Imported"] = len(new_rows)
            p["rep"]["DateFrom"] = min(tx_dates) if tx_dates else ""
            p["rep"]["DateTo"] = max(tx_dates) if tx_dates else ""
            p["rep"]["Status"] = "✅ Imported" if new_rows else "⏭️ No rows kept"

        _clear_data_caches()
        progress.progress(1.0, text="Complete.")
        filter_note = ""
        if only_closed:
            filter_note = ("Current-holdings filter: excluded " + str(len(excluded_names))
                           + " held security(ies) (" + str(filtered_out) + " row(s)); imported the rest.")
        st.session_state["_import_summary"] = {
            "person": person_label,
            "files_total": n,
            "files_ok": sum(1 for r in reports if r["Status"].startswith("✅")),
            "imported": imported,
            "skipped": skipped,
            "duplicates": duplicates,
            "companies": len(companies),
            "filter_note": filter_note,
            "files": reports,
        }
        st.rerun()

    _render_import_summary()

    with st.expander("Import equity transactions (PDF) for this person", expanded=False):
        st.caption(
            "Upload one or more ICICI equity transaction statement PDFs "
            "(e.g. TRX-Equity_27-04-2015_222545.pdf). Name-only statements without an ISIN column are supported."
        )
        only_closed = st.checkbox(
            "Only import fully-sold companies not in current holdings",
            value=True,
            key="people_eq_only_closed",
            help="Keeps only securities with zero net position and excludes ISINs still held in the latest holdings snapshot.",
        )
        ups = st.file_uploader("Equity statement PDF(s)", type=["pdf"], accept_multiple_files=True, key="people_eq_up")
        if ups:
            st.caption("Selected " + str(len(ups)) + " file(s): " + ", ".join(u.name for u in ups))
        if ups and st.button("Import equity statements", key="people_eq_import"):
            from src.pdf_parser import parse_pdf
            folder = Path("data/statements") / slug
            folder.mkdir(parents=True, exist_ok=True)
            _import_statement_files(ups, folder, parse_pdf, only_closed, slug + "__", source_kind="PDF")

    with st.expander("Import transactions (XLS/TSV export) for this person", expanded=False):
        st.caption("ICICI portfolio transaction export: ISIN Code, Action, Quantity, Transaction Price, Transaction Date.")
        st.info("🎯 Uploading transactions for: **" + person_label + "**", icon="👤")
        only_closed_x = st.checkbox("Only fully sold companies (net position = 0)", key="people_xls_only_closed")
        xups = st.file_uploader("Transaction file(s)", type=["xls", "xlsx", "csv", "tsv"], accept_multiple_files=True, key="people_xls_up")
        if xups:
            st.caption("Selected " + str(len(xups)) + " file(s): " + ", ".join(u.name for u in xups))
        if xups and st.button("Import transactions", key="people_xls_import"):
            from importers.xls.icici_transactions import parse_transactions_xls
            folder = Path("data/statements") / slug
            folder.mkdir(parents=True, exist_ok=True)
            _import_statement_files(xups, folder, parse_transactions_xls, only_closed_x, slug + "__", source_kind="XLS")

    # --- Imported Files Audit Table (below XLS uploader) ---
    st.markdown("#### Recently Imported Transaction Files")
    view_all = st.checkbox("View all portfolios", key="xls_audit_view_all", value=False)
    audit_pid = None if view_all else pid
    try:
        tx_rows = store.all_transactions(person_id=audit_pid)
    except Exception:  # noqa: BLE001
        tx_rows = []
    if tx_rows:
        from collections import defaultdict
        by_file: dict[str, list[dict]] = defaultdict(list)
        for r in tx_rows:
            by_file[str(r.get("source_file") or "")].append(r)
        prefix = slug + "__"
        person_map = {int(p["id"]): str(p["name"]) for p in store.list_people()}
        audit_rows = []
        for src_file, rows in sorted(by_file.items(), key=lambda kv: kv[0]):
            display_name = src_file.removeprefix(prefix)
            dates = [str(r.get("trade_date") or r.get("settlement_date") or "")[:10] for r in rows if r.get("trade_date") or r.get("settlement_date")]
            date_range = (min(dates) + " → " + max(dates)) if dates else ""
            pname = person_label if not view_all else person_map.get(int(rows[0].get("person_id") or -1), "Unknown")
            audit_rows.append({
                "Portfolio": pname,
                "File Name": display_name,
                "Trades": len(rows),
                "Date Range": date_range,
            })
        st.dataframe(pd.DataFrame(audit_rows), hide_index=True, width="stretch")
    else:
        st.caption("No imported transaction files yet.")

    with st.expander("Set 1 Jan 2014 as buy date for unmatched sells", expanded=False):
        st.caption("For every security with sells but missing buys, inserts a synthetic BUY dated 2014-01-01 priced at the 2014-01-01 Yahoo close. Securities without a 2014 price can be dropped.")
        drop_unpriced = st.checkbox("Drop securities with no 2014 price", value=False, key="u2014_drop")
        if st.button("Set 1 Jan 2014 as buy date for unmatched sells", key="u2014_run"):
            from app.context import DEFAULT_MARKET_DB, _xirr_db
            from src.unmatched_remediation import set_2014_buy_date
            bar = st.progress(0.0, text="Fetching 2014 prices...")
            def _prog(done, total, key):
                bar.progress(min(1.0, (done / total) if total else 1.0), text="Pricing " + str(done) + "/" + str(total) + ": " + str(key))
            try:
                res = set_2014_buy_date(_xirr_db(), pid, DEFAULT_MARKET_DB, drop_unpriced=drop_unpriced, progress=_prog)
                _clear_data_caches()
                st.success("Unmatched " + str(res["unmatched"]) + "; priced " + str(res["priced"]) + "; dropped " + str(res["dropped"]) + "; skipped " + str(res["skipped"]) + "; added cost " + str(int(res["total_cost"])) + ".")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error("2014 buy-date remediation failed: " + str(e))
    with st.expander("Import holdings statement (XLS/CSV) for this person", expanded=False):
        hup = st.file_uploader("Holdings file", type=["xls", "xlsx", "csv", "tsv"], key="people_hold_up")
        if hup and st.button("Import holdings", key="people_hold_import"):
            from src.holdings import load_holdings
            folder = Path("data/portfolio") / slug
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / hup.name
            with open(dest, "wb") as fh:
                fh.write(hup.getbuffer())
            try:
                df = load_holdings(str(dest))
                if df is None or df.empty:
                    raise ValueError("No holdings rows parsed from this file") 
                if df["current_price"].isna().all() and df["current_value"].isna().all():
                    raise ValueError("No price/value columns found - this looks like a transaction file, not a holdings statement")
                hrows = df.to_dict("records")
                try:
                    as_of = str(df.attrs.get("as_of_date"))[:10] if df.attrs.get("as_of_date") else date.today().isoformat()
                except Exception:  # noqa: BLE001
                    as_of = date.today().isoformat()
                store.add_portfolio_snapshot(str(dest), as_of, "equity", hrows, person_id=pid)
                _clear_data_caches()
                st.success("Imported " + str(len(hrows)) + " holdings for " + person_label + ".")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error("Holdings import failed: " + str(e))

    with st.expander("Sector mapping & enrichment", expanded=False):
        from app.shared import load_holdings_df as _load_holdings_df
        from market_data import sector as _sector
        hdf = _load_holdings_df(person_id=pid)
        if hdf is None or getattr(hdf, "empty", True):
            st.info("Import a holdings statement for this person first.")
        else:
            srows = _sector.holding_status(hdf)
            st.dataframe(pd.DataFrame(srows), hide_index=True, width="stretch")
            missing = sorted({r["isin"] for r in srows if r["status"] == "needs symbol"})
            if missing:
                st.caption("Unmapped ISINs need a yfinance symbol (NSE: TIPIND.NS, BSE: TIPIND.BO).")
                with st.form("sector_symbol_form"):
                    sel_isin = st.selectbox("ISIN to map", missing, key="sector_map_isin")
                    sym = st.text_input("yfinance symbol", key="sector_map_symbol", placeholder="e.g. TIPIND.NS")
                    exch = st.selectbox("Exchange", ["NSE", "BSE"], key="sector_map_exchange")
                    if st.form_submit_button("Save symbol mapping"):
                        try:
                            saved = _sector.upsert_symbol_mapping(sel_isin, sym, exch)
                            _clear_data_caches()
                            st.success("Mapped " + sel_isin + " -> " + saved)
                            st.rerun()
                        except Exception as e:  # noqa: BLE001
                            st.error("Could not save mapping: " + str(e))
            if st.button("Fetch sectors from yfinance", key="sector_fetch"):
                isins = [str(x).strip().upper() for x in hdf["isin"].dropna().unique().tolist()]
                with st.spinner("Fetching sectors..."):
                    res = _sector.enrich_sectors_for_isins(isins)
                    _sector.upsert_sector_mapping(res)
                ok = sum(1 for v in res.values() if v.get("sector"))
                n_map = sum(1 for v in res.values() if v.get("error") == "no_symbol_mapping")
                _clear_data_caches()
                st.success("Sectors fetched for " + str(ok) + " ISINs; " + str(n_map) + " need symbol mapping.")
                st.rerun()

    st.caption("MF statements are uploaded once in the Mutual Fund Data Import section "
               "(Data & Settings → Mutual Fund Data Import & NAV Sync).")
