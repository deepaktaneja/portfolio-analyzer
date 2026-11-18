"""Page 3 — Holdings & Research.

Unified Equity + MF holdings table with a detail panel on selection (transactions,
corporate actions, XIRR, profit summary) plus AI research report generation and
research history. Consolidates the former Stock Analysis, MF Holdings, Research
Report and Research History tabs.
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from app.components import ui
from app.context import AppContext
from app.shared import fmt_pct, inr, security_name


def _holdings_table(holdings) -> pd.DataFrame:
    if holdings is None or getattr(holdings, "empty", True):
        return pd.DataFrame()
    df = holdings.copy()
    if "isin" in df.columns:
        df["security_name"] = df["isin"].map(lambda x: security_name(x, holdings=holdings))
    if {"current_value", "quantity", "avg_price"} <= set(df.columns):
        try:
            inv = df["quantity"].astype(float) * df["avg_price"].astype(float)
            df["unrealized_gain"] = df["current_value"].astype(float) - inv
        except (TypeError, ValueError):
            pass
    return df


def _render_detail_panel(ctx: AppContext, isin: str) -> None:
    """Detailed investment view for a single equity holding (former Stock Analysis)."""
    txs, holdings, pl = ctx.txs, ctx.holdings, ctx.pl
    st.markdown(f"#### Detail: {security_name(isin, txs, holdings)} ({isin})")

    from accounting.fifo import get_stock_profit_summary
    from ledger.holdings_service import get_stock_quantity as get_qty_actions
    from ledger.positions import get_transaction_history
    from performance.xirr import calculate_stock_xirr
    from src.store import SQLiteStore

    store = SQLiteStore(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3"))
    openings = store.get_opening_positions(person_id=ctx.filters.person_id)
    hist = get_transaction_history(txs, isin)
    st.markdown("##### Transactions & corporate-action history")
    ui.safe_dataframe(pd.DataFrame(hist), empty_message="No transactions for this security.")

    from market_data.repository import MarketDataStore
    _actions = MarketDataStore(ctx.market_db_path).get_corporate_actions()
    try:
        qty = get_qty_actions(txs, isin, "2099-12-31", opening_positions=openings, actions=_actions)
    except ValueError:
        qty = 0.0
        st.warning("Negative holdings detected for this stock; opening position may be missing.")
    st.metric("Current Quantity", qty)

    _broker_qty = None
    if holdings is not None and not holdings.empty:
        _m = holdings[holdings["isin"].astype(str).str.upper() == str(isin).strip().upper()]
        if not _m.empty:
            _broker_qty = float(_m.iloc[0].get("quantity") or 0)
    if _broker_qty is not None:
        st.metric("Broker Quantity", _broker_qty)
        st.metric("Difference (reconstructed - broker)", qty - _broker_qty)

    res = calculate_stock_xirr(txs, security_id=isin, end_date=ctx.filters.end_date, price_lookup=pl)
    _x = res.get("xirr")
    st.metric("Stock XIRR", fmt_pct(_x))
    if _x is None and res.get("reason"):
        ui.estimated_xirr_note(res.get("reason"))
    summary = get_stock_profit_summary(txs, security_id=isin, as_of_date=ctx.filters.end_date, price_lookup=pl)
    st.json(summary)

    # ---- Decision signals for this holding (shadow mode) -------------------
    st.markdown("##### Decision Signals (shadow mode)")
    try:
        from decision.ui import compute_recommendations, render_recommendation_detail
        recs = st.session_state.get("decision_recs")
        if recs is None:
            recs = compute_recommendations(ctx)
            st.session_state["decision_recs"] = recs
        match = next((r for r in recs.get("equity", []) if r.get("security_id") == str(isin).strip().upper()), None)
        if match:
            render_recommendation_detail(match, str(isin))
        else:
            st.caption("No decision signals available for this holding.")
    except Exception as e:  # noqa: BLE001
        st.caption(f"Decision signals unavailable: {e}")


def _render_research() -> None:
    with st.expander("AI Research — Generate Report", expanded=False):
        st.info("AI-powered research using MCP server data and LLM analysis. No buy/sell/trim/exit signals (reserved for Stage 8).")
        col1, col2 = st.columns([2, 1])
        with col1:
            research_symbol = st.text_input("Symbol (e.g. RELIANCE, TCS)", value="", key="research_symbol")
        with col2:
            research_isin = st.text_input("ISIN (optional)", value="", key="research_isin")
        run_research = st.button("Generate Research Report", key="run_research")
        if run_research and research_symbol:
            _generate_research(research_symbol, research_isin or None)
        if "last_research_md" in st.session_state:
            st.markdown(st.session_state["last_research_md"])
            if st.button("Export to Markdown", key="export_research"):
                from research.report import export_report_markdown
                fname = f"research_{st.session_state.get('last_research_report', {}).get('symbol', 'holding')}.md"
                path = export_report_markdown(st.session_state["last_research_md"], fname)
                st.success(f"Exported to {path}")


def _generate_research(symbol: str, isin: str | None) -> None:
    with st.spinner("Gathering MCP evidence and generating research report..."):
        try:
            import asyncio

            from research.forecast import ForecastEngine
            from research.orchestrator import ResearchOrchestrator
            from research.portfolio import PortfolioResearchIntegrator
            from research.report import ResearchReportBuilder

            orchestrator = ResearchOrchestrator()
            report = asyncio.run(orchestrator.generate_report(symbol=symbol, isin=isin))
            forecast_dict = ForecastEngine().analyze(
                symbol=symbol, current_price=0, historical={}, analyst_consensus={},
            ).to_dict()
            integrator = PortfolioResearchIntegrator()
            portfolio_ctx = integrator.build_portfolio_context(symbols=[symbol], isins={symbol: isin})
            portfolio_dict = {
                "total_invested": float(portfolio_ctx.total_invested),
                "total_current_value": float(portfolio_ctx.total_current_value),
                "total_unrealized_gain": float(portfolio_ctx.total_unrealized_gain),
                "portfolio_xirr": portfolio_ctx.portfolio_xirr,
                "concentration_risk": portfolio_ctx.concentration_risk,
                "valuation_risk": portfolio_ctx.valuation_risk,
                "fundamental_risk": portfolio_ctx.fundamental_risk,
            }
            md = ResearchReportBuilder.build_markdown(
                symbol=symbol, isin=isin, orchestrator_output=report,
                forecast=forecast_dict, portfolio_context=portfolio_dict,
            )
            st.session_state["last_research_md"] = md
            st.session_state["last_research_report"] = report
            st.success("Research report generated.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Research generation failed: {e}")


def _render_history() -> None:
    with st.expander("Research History", expanded=False):
        st.caption("Historical research snapshots to track changes in fundamentals and outlook.")
        try:
            from models.research import ResearchReport, get_research_session
            session = get_research_session()
            reports = session.query(ResearchReport).order_by(ResearchReport.generated_at.desc()).limit(20).all()
            session.close()
            if reports:
                for r in reports:
                    with st.expander(f"{r.title} — {r.generated_at[:16]} ({r.model})"):
                        st.write(f"**Symbol:** {r.security_id or 'N/A'}")
                        st.write(f"**Evidence IDs:** {r.evidence_ids}")
                        st.markdown(r.content_md[:2000] + ("..." if len(r.content_md) > 2000 else ""))
            else:
                st.info("No research reports stored yet.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Unable to load research history: {e}")


def render(ctx: AppContext) -> None:
    import pandas as pd

    from app.mf_ui import scheme_table
    from app.pages.portfolio import (
        _MF_DIV,
        _MF_IN,
        _MF_OUT,
        _blended_xirr,
        _mf_cost_basis_upto,
        _mf_scope,
        _mf_value_at,
        _period_bounds,
        _year_options,
    )
    f = ctx.filters
    st.markdown("### Mutual Funds")
    st.caption("Mutual fund returns, holdings, overlap and quality.")
    mf, nav_by_scheme, mf_txs, _folder = _mf_scope(ctx)
    if not mf.get("has_mf_data"):
        st.info("Import MF statements + NAVs in Data & Settings to view MF analytics.")
    else:
        opts = _year_options(ctx.txs, mf_txs, f)
        if st.session_state.get("mf3_period") not in opts:
            st.session_state["mf3_period"] = "Since inception"
        sel = st.selectbox("Period", opts, key="mf3_period")
        ps, pe = _period_bounds(f, sel)
        valid = ps is not None and pe is not None and ps <= pe
        mf_val = float(mf.get("total_value") or 0)
        mf_inv = float(mf.get("total_invested") or 0)
        mf_xirr = None
        if valid:
            mf_val = _mf_value_at(mf_txs, nav_by_scheme, pe)
            mf_inv = _mf_cost_basis_upto(mf_txs, pe)
            me_s = str(pe)[:10]
            flows = []
            for t in mf_txs or []:
                d = str(t.get("trade_date") or "")[:10]
                if not d or d > me_s:
                    continue
                side = str(t.get("side") or "").upper()
                amt = abs(float(t.get("amount") or 0))
                if side in _MF_IN:
                    flows.append((d, -amt))
                elif side in _MF_OUT or side in _MF_DIV:
                    flows.append((d, amt))
            if mf_val > 0:
                flows.append((me_s, mf_val))
            mf_xirr = _blended_xirr([], flows)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("MF Value", inr(mf_val))
        c2.metric("Invested", inr(mf_inv))
        c3.metric("Gain", inr(mf_val - mf_inv))
        c4.metric("MF XIRR", fmt_pct(mf_xirr) if mf_xirr is not None else "N/A")
        if valid:
            st.caption(f"Values as of {str(pe)[:10]} (cost basis + latest NAV).")
        df = scheme_table(mf)
        if not df.empty:
            disp = df.copy()
            for col in ("xirr", "1y", "3y", "5y", "volatility", "max_drawdown"):
                if col in disp.columns:
                    disp[col] = disp[col].map(lambda v: f"{v * 100:.1f}%" if isinstance(v, (int, float)) else v)
            ui.safe_dataframe(disp)
        ac = mf.get("asset_class_concentration", {}).get("buckets", {})
        if ac:
            st.markdown("##### Asset-class concentration")
            st.bar_chart(pd.Series(ac))
        pairs = mf.get("overlap", {}).get("pairs", [])
        if pairs:
            st.markdown("##### Fund overlap")
            odf = pd.DataFrame(pairs)[["scheme_a", "scheme_b", "common_count", "jaccard", "weight_overlap_pct"]]
            ui.safe_dataframe(odf)
            for p in mf.get("overlap_flagged", []):
                st.warning(f"High overlap: {p[chr(115)+chr(99)+chr(104)+chr(101)+chr(109)+chr(101)+chr(95)+chr(97)]} / {p[chr(115)+chr(99)+chr(104)+chr(101)+chr(109)+chr(101)+chr(95)+chr(98)]} ({p[chr(119)+chr(101)+chr(105)+chr(103)+chr(104)+chr(116)+chr(95)+chr(111)+chr(118)+chr(101)+chr(114)+chr(108)+chr(97)+chr(112)+chr(95)+chr(112)+chr(99)+chr(116)]:.1f}%).")
        qrows = mf.get("quality", [])
        if qrows:
            st.markdown("##### Fund quality assessment")
            qdf = pd.DataFrame([{"scheme": q.get("scheme_name") or q.get("scheme_code"), "rating": q.get("rating"), "score": q.get("overall_score")} for q in qrows])
            ui.safe_dataframe(qdf)

    try:
        from copilot.ui import render_copilot
        render_copilot(ctx, "holdings")
    except Exception:  # noqa: BLE001, S110
        pass
