"""Page 2 - Direct Equity.

Direct equity portfolio performance vs benchmark, current value / invested / XIRR
with a period selector, cumulative growth vs Nifty 50/500 TRI, equity sector &
concentration breakdowns, risk-adjusted metrics, gains & dividends.
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from app.components import ui
from app.context import AppContext
from app.shared import fmt_pct, format_df_one_decimal, inr, security_name, tx_hash


def _idx_lookup(market_db):
    from market_data.prices import get_index_tri

    def _idx50(d):
        try:
            return get_index_tri("NIFTY50TRI", str(d), db_path=market_db)
        except Exception:  # noqa: BLE001
            return None

    def _idx500(d):
        try:
            return get_index_tri("NIFTY500TRI", str(d), db_path=market_db)
        except Exception:  # noqa: BLE001
            return None

    return _idx50, _idx500


def _render_annual(ctx: AppContext) -> None:
    txs, pl, market_db = ctx.txs, ctx.pl, ctx.market_db_path
    f = ctx.filters
    ui.section("Annual Performance")
    from market_data.repository import MarketDataStore
    _mdr = MarketDataStore(market_db).get_market_data_quality_report()
    if not _mdr.get("securities"):
        st.warning("No market data imported; TWR/unrealized values unavailable.")
    _idx50, _idx500 = _idx_lookup(market_db)
    from performance.benchmarks import calculate_annual_returns
    from src.store import SQLiteStore
    _o = SQLiteStore(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3")).get_opening_positions(person_id=f.person_id)
    _cache_key = ("annual", str(f.start_date), str(f.end_date), tx_hash(txs), tx_hash(_o) if _o else "")
    rows = st.session_state.get(_cache_key)
    if rows is None:
        try:
            with st.spinner("Calculating annual performance..."):
                rows = calculate_annual_returns(
                    txs, price_lookup=pl, index_lookup_50=_idx50, index_lookup_500=_idx500,
                    start_date=f.start_date, end_date=f.end_date, frequency="calendar", opening_positions=_o,
                )
            st.session_state[_cache_key] = rows
        except Exception as e:  # noqa: BLE001
            st.error(f"Annual Performance unavailable: {e}")
            rows = None
    if rows is not None:
        ui.safe_dataframe(pd.DataFrame(rows), empty_message="No annual performance rows.")
        if rows and all(r.get("portfolio_twr") is None for r in rows):
            st.warning("TWR unavailable: missing price coverage for the selected period.")
        if not any(r.get("nifty50_tri_return") is not None for r in rows):
            st.warning("Nifty TRI data not available. Import Nifty TRI history to see benchmark returns.")


def _render_benchmark(ctx: AppContext) -> None:
    txs, pl, market_db = ctx.txs, ctx.pl, ctx.market_db_path
    f = ctx.filters
    ui.section("Benchmark Comparison")
    try:
        from market_data.prices import get_benchmark_sync_status, sync_benchmark_history
        for _iid in ("NIFTY50TRI", "NIFTY500TRI"):
            st.write(_iid, get_benchmark_sync_status(_iid, db_path=market_db))
        if st.button("Sync Nifty TRI (last 5 years)", key="sync_tri_bench"):
            from datetime import date
            _end = date.today()
            _start = _end.replace(year=_end.year - 5)
            for _iid in ("NIFTY50TRI", "NIFTY500TRI"):
                try:
                    st.json(sync_benchmark_history(_iid, str(_start), str(_end), db_path=market_db))
                except Exception as e:  # noqa: BLE001
                    st.error(str(e))
            st.rerun()
    except Exception as e:  # noqa: BLE001
        st.warning(str(e))
    _idx50, _idx500 = _idx_lookup(market_db)
    from performance.benchmarks import compare_portfolio_benchmarks
    from src.store import SQLiteStore
    _o = SQLiteStore(os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3")).get_opening_positions(person_id=f.person_id)
    _bkey = ("benchmark", str(f.start_date), str(f.end_date), tx_hash(txs), tx_hash(_o) if _o else "")
    bres = st.session_state.get(_bkey)
    if bres is None:
        try:
            with st.spinner("Calculating benchmark comparison..."):
                bres = compare_portfolio_benchmarks(
                    txs, price_lookup=pl, index_lookup_50=_idx50, index_lookup_500=_idx500,
                    start_date=f.start_date, end_date=f.end_date, opening_positions=_o,
                )
            st.session_state[_bkey] = bres
        except Exception as e:  # noqa: BLE001
            st.error(f"Benchmark Analysis unavailable: {e}")
            bres = None
    if bres is not None:
        st.json(bres)
        if bres.get("methodology_time_weighted", {}).get("nifty50_tri_return") is None:
            st.warning("Benchmark TRI data not available. Import Nifty TRI history to see benchmark returns.")


def _render_xirr_diagnostics_section(ctx: AppContext) -> None:
    """Cash-flow audit, timing-vs-selection decomposition and risk metrics."""
    from app.pages.portfolio import render_xirr_diagnostics
    from performance.xirr import calculate_portfolio_xirr

    txs, pl = ctx.txs, ctx.pl
    f = ctx.filters
    if not txs:
        ui.section("XIRR Diagnostics")
        st.info("No transactions available for XIRR diagnostics.")
        return
    _dkey = ("xirr_diag", str(f.start_date), str(f.end_date), tx_hash(txs))
    res = st.session_state.get(_dkey)
    if res is None:
        try:
            with st.spinner("Preparing XIRR diagnostics..."):
                res = calculate_portfolio_xirr(
                    txs,
                    actions=ctx.corporate_actions,
                    dividends=ctx.dividend_flows,
                    price_lookup=pl,
                    start_date=f.start_date,
                    end_date=f.end_date,
                    opening_positions=ctx.opening_positions,
                )
            st.session_state[_dkey] = res
        except Exception as e:  # noqa: BLE001
            st.error(f"XIRR diagnostics unavailable: {e}")
            return
    render_xirr_diagnostics(ctx, res, f.start_date, f.end_date, key_prefix="performance")


def _render_gains_dividends(ctx: AppContext) -> None:
    txs, holdings, pl, market_db = ctx.txs, ctx.holdings, ctx.pl, ctx.market_db_path
    f = ctx.filters
    ui.section("Gains & Dividends")
    from accounting.fifo import calculate_realized_gains, calculate_unrealized_gains
    from corporate_actions.models import CorporateAction
    from market_data.repository import MarketDataStore as _MDS

    _acts = _MDS(market_db).get_corporate_actions()

    def _conv(r):
        from ledger.holdings_service import _parse_date
        return CorporateAction(
            security_id=r["security_id"], event_type=r["event_type"],
            ex_date=_parse_date(r.get("ex_date")), effective_date=_parse_date(r.get("effective_date")),
            ratio=r.get("ratio"), successor_id=r.get("successor_id"), verified=bool(r.get("verified")),
            source=r.get("source"), verification_status=r.get("verification_status"),
        )

    _ca_objs = [_conv(r) for r in _acts]
    try:
        from corporate_actions.engine import _dedupe_actions
        _ca_objs = _dedupe_actions(_ca_objs)
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        with st.spinner("Calculating gains..."):
            rg = calculate_realized_gains(txs, actions=_ca_objs, start_date=f.start_date, end_date=f.end_date)
            ug = calculate_unrealized_gains(txs, actions=_ca_objs, as_of_date=f.end_date, price_lookup=pl)
        if isinstance(rg, list) and rg:
            _rdf = pd.DataFrame(rg)
            if "security_id" in _rdf.columns:
                _rdf["security_name"] = _rdf["security_id"].map(lambda x: security_name(x, txs, holdings))
            st.markdown("##### Realised Gains")
            ui.safe_dataframe(format_df_one_decimal(_rdf))
        else:
            st.info("No realized gains records available.")
        if isinstance(ug, list) and ug:
            _udf = pd.DataFrame(ug)
            if "security_id" in _udf.columns:
                _udf["security_name"] = _udf["security_id"].map(lambda x: security_name(x, txs, holdings))
            st.markdown("##### Unrealised Gains")
            ui.safe_dataframe(format_df_one_decimal(_udf))
        else:
            st.info("No unrealized gains records available.")
        try:
            _divs = _MDS(market_db).get_dividend_events()
        except Exception as e:  # noqa: BLE001
            _divs = []
            st.warning(f"Dividend records unavailable: {e}")
        if _divs:
            st.markdown("##### Dividend / IDCW Records")
            ui.safe_dataframe(pd.DataFrame(_divs))
        else:
            st.warning("No dividend records found; dividend income is not assumed to be zero.")
        if isinstance(ug, list):
            _missing = [r for r in ug if r.get("price") is None]
            if _missing:
                st.warning(f"Missing cost-basis/price data for {len(_missing)} position(s); gains may be provisional.")
    except Exception as e:  # noqa: BLE001
        st.error(f"Gains & Dividends unavailable: {e}")


def _render_signal_history(ctx: AppContext) -> None:
    ui.section("Signal History & Outcomes")
    try:
        from decision.storage import list_history, list_recent_recommendations
        recs = list_recent_recommendations(limit=25)
        if recs:
            import pandas as pd
            df = pd.DataFrame([
                {
                    "security": r.security_id,
                    "type": r.security_type,
                    "action": r.action,
                    "confidence": round(float(r.confidence or 0), 2),
                    "my_decision": r.user_decision,
                    "shadow": r.is_shadow,
                    "created": r.created_at[:19],
                }
                for r in recs
            ])
            ui.safe_dataframe(df)
        else:
            st.caption("No saved recommendations yet — acknowledge a signal in Holdings & Research to record it.")
        hist = list_history(limit=25)
        if hist:
            import pandas as pd
            hdf = pd.DataFrame([
                {"security": h.security_id, "decision": h.user_decision, "decided": h.decided_at[:19],
                 "outcome": h.outcome}
                for h in hist
            ])
            st.markdown("##### Recorded decisions")
            ui.safe_dataframe(hdf)
        st.caption("Signals run in shadow mode. Validation against subsequent prices is available via the "
                   "backtest engine (decision/backtest.py); signals with missing price data are flagged there.")
    except Exception as e:  # noqa: BLE001
        st.caption(f"Signal history unavailable: {e}")


def _render_sector_breakdowns(ctx: AppContext) -> None:
    """Equity-only sector / concentration breakdowns."""

    ui.section("Equity Breakdowns — Sector & Concentration")
    f = ctx.filters
    if not f.show_equity:
        return
    if ctx.holdings is None or getattr(ctx.holdings, "empty", True):
        st.caption("No equity holdings imported.")
        return
    hdf = ctx.holdings.copy()
    if "isin" in hdf.columns:
        hdf["security_name"] = hdf["isin"].map(lambda x: security_name(x, holdings=ctx.holdings))
    hdf = hdf.sort_values("current_value", ascending=False, na_position="last")
    total = float(hdf["current_value"].fillna(0).sum()) or 0.0
    if total > 0:
        hdf["weight_%"] = (hdf["current_value"].fillna(0) / total * 100).round(2)
    st.markdown("##### Top Holdings")
    cols = [c for c in ["security_name", "isin", "quantity", "current_value", "weight_%"] if c in hdf.columns]
    ui.safe_dataframe(hdf[cols].head(10), empty_message="No holdings to display.")
    if "weight_%" in hdf.columns:
        conc = hdf[hdf["weight_%"] > 25]
        if not conc.empty:
            for _, r in conc.iterrows():
                st.warning(f"Concentration risk: {r.get(chr(115)+chr(101)+chr(99)+chr(117)+chr(114)+chr(105)+chr(116)+chr(121)+chr(95)+chr(110)+chr(97)+chr(109)+chr(101)) or r.get(chr(105)+chr(115)+chr(105)+chr(110))} is {r[chr(119)+chr(101)+chr(105)+chr(103)+chr(104)+chr(116)+chr(95)+chr(37)]:.1f}% (> 25%).")
        else:
            st.success("No single holding exceeds the 25% concentration threshold.")
    if "sector" in hdf.columns and hdf["sector"].notna().any():
        st.markdown("##### Sector exposure")
        sec = hdf.groupby("sector", dropna=True)["current_value"].sum().sort_values(ascending=False)
        from app.components.charts import render_sector_chart
        fig = render_sector_chart(sec)
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
    else:
        st.caption("Sector data not available in the holdings statement.")


def _render_attention(ctx: AppContext) -> None:
    f = ctx.filters
    if not f.show_equity:
        return
    st.markdown("#### Investments Requiring Attention")
    st.caption("Advisory signals (shadow mode): HOLD / MONITOR / TRIM_REVIEW / EXIT_REVIEW / REPLACEMENT_REVIEW. No trades are executed.")
    try:
        from decision.ui import attention_items, compute_recommendations, recommendations_frame
        recs = st.session_state.get("decision_recs")
        if recs is None:
            with st.spinner("Evaluating decision signals (shadow mode)..."):
                recs = compute_recommendations(ctx)
            st.session_state["decision_recs"] = recs
        items = attention_items(recs)
        if items:
            ui.safe_dataframe(recommendations_frame(items))
        else:
            st.success("No holdings currently require action.")
    except Exception:  # noqa: BLE001
        st.caption("Decision signals unavailable")


def _render_equity_metrics(ctx: AppContext) -> None:
    from app.pages.portfolio import _period_bounds, _year_options
    from performance.xirr import calculate_portfolio_snapshot, calculate_portfolio_xirr
    txs = ctx.txs
    f = ctx.filters
    st.markdown("#### Direct Equity Performance")
    opts = _year_options(txs, [], f)
    if st.session_state.get("eq_period") not in opts:
        st.session_state["eq_period"] = "Since inception"
    sel = st.selectbox("Period", opts, key="eq_period")
    p_start, p_end = _period_bounds(f, sel)
    valid = p_start is not None and p_end is not None and p_start <= p_end
    val = inv = 0.0
    if txs and valid:
        try:
            snap = calculate_portfolio_snapshot(txs, price_lookup=ctx.pl, as_of_date=p_end, opening_positions=ctx.opening_positions)
            val = float(snap.get("portfolio_value") or 0)
            inv = float(snap.get("invested_capital") or 0)
        except Exception:  # noqa: BLE001, S110
            pass
    xirr = None
    if txs and valid:
        try:
            res = calculate_portfolio_xirr(txs, actions=ctx.corporate_actions, dividends=ctx.dividend_flows,
                                           start_date=p_start, end_date=p_end, price_lookup=ctx.pl,
                                           opening_positions=ctx.opening_positions)
            xirr = res.get("xirr")
        except Exception:  # noqa: BLE001
            xirr = None
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current Value", inr(val))
    c2.metric("Invested Capital", inr(inv))
    c3.metric("Returns", inr(val - inv))
    c4.metric("XIRR", fmt_pct(xirr) if xirr is not None else "N/A")
    if valid:
        st.caption(f"As of {str(p_end)[:10]} - period-end reconstruction (cost basis + market value).")

def _render_equity_growth_chart(ctx: AppContext) -> None:
    from datetime import date as _d

    import plotly.graph_objects as go

    from app.pages.portfolio import _cumulative_series, _period_bounds, _year_options
    from market_data.prices import get_index_tri
    from performance.xirr import calculate_portfolio_snapshot
    txs = ctx.txs
    f = ctx.filters
    st.markdown("#### Return vs Benchmark")
    opts = _year_options(txs, [], f)
    if st.session_state.get("eq_chart_period") not in opts:
        st.session_state["eq_chart_period"] = "Since inception"
    sel = st.selectbox("Chart period", opts, key="eq_chart_period")
    p_start, p_end = _period_bounds(f, sel)
    valid = p_start is not None and p_end is not None and p_start <= p_end
    if not valid or not txs:
        st.caption("No equity data for this period.")
        return
    s = p_start if isinstance(p_start, _d) else _d.fromisoformat(str(p_start)[:10])
    e = p_end if isinstance(p_end, _d) else _d.fromisoformat(str(p_end)[:10])
    rows = []
    for y in range(s.year, e.year + 1):
        ys, ye = max(_d(y, 1, 1), s), min(_d(y, 12, 31), e)
        ev = 0.0
        try:
            es = calculate_portfolio_snapshot(txs, price_lookup=ctx.pl, as_of_date=ye, opening_positions=ctx.opening_positions)
            ev = float(es.get("portfolio_value") or 0)
        except Exception:  # noqa: BLE001
            ev = 0.0
        eqflow = 0.0
        for t in (txs or []):
            td = str(t.get("trade_date") or t.get("settlement_date") or "")[:10]
            if not td or not (str(ys)[:10] <= td <= str(ye)[:10]):
                continue
            amt = float(t.get("net_amount") or 0)
            side = str(t.get("buy_sell") or "").upper()
            if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
                eqflow += amt
            elif side == "SELL":
                eqflow -= amt
        r50 = r500 = None
        a50 = get_index_tri("NIFTY50TRI", str(ys)[:10], db_path=ctx.market_db_path)
        b50 = get_index_tri("NIFTY50TRI", str(ye)[:10], db_path=ctx.market_db_path)
        if a50 and b50:
            r50 = float(b50) / float(a50) - 1.0
        a500 = get_index_tri("NIFTY500TRI", str(ys)[:10], db_path=ctx.market_db_path)
        b500 = get_index_tri("NIFTY500TRI", str(ye)[:10], db_path=ctx.market_db_path)
        if a500 and b500:
            r500 = float(b500) / float(a500) - 1.0
        rows.append({"period": str(y), "portfolio_value": ev, "net_flow": eqflow,
                     "nifty50_tri_return": r50, "nifty500_tri_return": r500})
    xs, ypf, y50, y500, has50, has500 = _cumulative_series(rows)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=ypf, mode="lines+markers", name="Direct Equity"))
    if has50:
        fig.add_trace(go.Scatter(x=xs, y=y50, mode="lines+markers", name="Nifty 50 TRI"))
    if has500:
        fig.add_trace(go.Scatter(x=xs, y=y500, mode="lines+markers", name="Nifty 500 TRI"))
    fig.update_layout(title="Direct Equity cumulative growth vs benchmark", yaxis_title="Cumulative return (%)",
                      height=380, legend={"orientation": "h"}, margin={"t": 50, "b": 40, "l": 55, "r": 20},
                      plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, width="stretch")


def render(ctx: AppContext) -> None:
    f = ctx.filters
    st.markdown("### Direct Equity")
    st.caption("Direct equity portfolio performance vs benchmark, with sector, concentration and risk breakdowns.")
    if not f.show_equity:
        st.info("Select Direct Equity in the sidebar Asset type filter to view this page.")
    else:
        _render_equity_metrics(ctx)
        st.divider()
        _render_equity_growth_chart(ctx)
        st.divider()
        _render_sector_breakdowns(ctx)
        st.divider()
        _render_attention(ctx)
        st.divider()
        _render_xirr_diagnostics_section(ctx)
        st.divider()
        _render_gains_dividends(ctx)
        st.divider()
        _render_signal_history(ctx)

    try:
        from copilot.ui import render_copilot
        render_copilot(ctx, "performance")
    except Exception:  # noqa: BLE001, S110
        pass
