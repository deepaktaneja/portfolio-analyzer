"""Page 2 - Direct Equity.

Direct equity portfolio performance vs benchmark, current value / invested / XIRR
with a period selector, cumulative growth vs Nifty 50/500 TRI, equity sector &
concentration breakdowns, risk-adjusted metrics, gains & dividends.
"""
from __future__ import annotations

import math
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
    from market_data.prices import get_index_tri
    from performance import equity_analytics as ea
    txs = ctx.txs
    f = ctx.filters
    st.markdown("#### Direct Equity Performance")

    ctl1, ctl2 = st.columns(2)
    with ctl1:
        opts = _year_options(txs, [], f)
        if st.session_state.get("eq_period") not in opts:
            st.session_state["eq_period"] = "Since inception"
        sel = st.selectbox("Period", opts, key="eq_period")
    with ctl2:
        bench_map = {"Nifty 50": "NIFTY50TRI", "Nifty 500": "NIFTY500TRI"}
        if st.session_state.get("eq_benchmark") not in bench_map:
            st.session_state["eq_benchmark"] = "Nifty 50"
        bench_label = st.selectbox("Benchmark", list(bench_map.keys()), key="eq_benchmark")
    bench_id = bench_map.get(bench_label, "NIFTY50TRI")

    p_start, p_end = _period_bounds(f, sel)
    valid = p_start is not None and p_end is not None and p_start <= p_end

    val = inv = 0.0
    xirr = None
    realized = 0.0
    divs = 0.0
    bench_ret = None
    if txs and valid:
        try:
            snap = ea.portfolio_snapshot(txs, ctx.pl, p_end, ctx.opening_positions)
            val = float(snap.get("portfolio_value") or 0)
            inv = float(snap.get("invested_capital") or 0)
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            xirr = ea.portfolio_xirr(txs, ctx.corporate_actions, ctx.dividend_flows, ctx.pl, p_start, p_end, ctx.opening_positions)
        except Exception:  # noqa: BLE001
            xirr = None
        try:
            realized = ea.realized_pnl(txs, actions=None, start_date=p_start, end_date=p_end)
        except Exception:  # noqa: BLE001
            realized = 0.0
        try:
            div_use = "net" if getattr(f, "dividend_mode", "gross") == "net" else "gross"
            divs = ea.total_dividends(ctx.dividend_flows, start_date=p_start, end_date=p_end, use=div_use)
        except Exception:  # noqa: BLE001
            divs = 0.0
        try:
            a = get_index_tri(bench_id, str(p_start)[:10], db_path=ctx.market_db_path)
            b = get_index_tri(bench_id, str(p_end)[:10], db_path=ctx.market_db_path)
            if a and b and float(a) > 0:
                days = max(1, (p_end - p_start).days)
                bench_ret = (float(b) / float(a)) ** (365.0 / days) - 1.0
        except Exception:  # noqa: BLE001
            bench_ret = None

    unreal = val - inv
    unreal_pct = (unreal / inv) if inv > 0 else None
    bench_alpha = ea.alpha(xirr, bench_ret)

    r1 = st.columns(4)
    r1[0].metric("Total Invested Cost", inr(inv))
    r1[1].metric("Current Market Value", inr(val))
    r1[2].metric("Unrealized P&L", inr(unreal))
    r1[3].metric("Unrealized P&L %", fmt_pct(unreal_pct) if unreal_pct is not None else "N/A")
    r2 = st.columns(4)
    r2[0].metric("Realized P&L", inr(realized))
    r2[1].metric("Total Dividends", inr(divs))
    r2[2].metric("Portfolio XIRR", fmt_pct(xirr) if xirr is not None else "N/A")
    r2[3].metric(f"Alpha vs {bench_label}", fmt_pct(bench_alpha) if bench_alpha is not None else "N/A")
    if valid:
        st.caption(f"As of {str(p_end)[:10]} - period-end reconstruction. Benchmark: {bench_label} (TRI, annualized). Dividends already included in XIRR.")

def _render_equity_growth_chart(ctx: AppContext) -> None:
    from datetime import date as _d
    from datetime import timedelta as _td

    import plotly.graph_objects as go

    from app.pages.portfolio import _period_bounds, _year_options
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
    def _cum(series):
        out = []
        base = None
        for v in series:
            try:
                fv = float(v) if v is not None else None
            except (TypeError, ValueError):
                fv = None
            if base is None and fv is not None and fv > 0:
                base = fv
            out.append(None if (base is None or fv is None) else round((fv / base - 1.0) * 100.0, 4))
        return out

    cache_key = ("eq_growth_monthly", tx_hash(txs), str(s)[:10], str(e)[:10],
                 getattr(f, "person_id", None), len(ctx.opening_positions or []))
    cached = st.session_state.get(cache_key)
    if cached is None:
        pts = {s, e}
        cur_first = _d(s.year, s.month, 1)
        while cur_first <= e:
            fn = _d(cur_first.year + 1, 1, 1) if cur_first.month == 12 else _d(cur_first.year, cur_first.month + 1, 1)
            me = fn - _td(days=1)
            if s <= me <= e:
                pts.add(me)
            cur_first = fn
        dates = sorted(pts)
        vals = []
        n50 = []
        n500 = []
        with st.spinner("Computing monthly growth vs benchmark..."):
            for d in dates:
                try:
                    snap = calculate_portfolio_snapshot(txs, price_lookup=ctx.pl, as_of_date=d, opening_positions=ctx.opening_positions)
                    vals.append(float(snap.get("portfolio_value") or 0.0))
                except Exception:  # noqa: BLE001
                    vals.append(0.0)
                n50.append(get_index_tri("NIFTY50TRI", str(d)[:10], db_path=ctx.market_db_path))
                n500.append(get_index_tri("NIFTY500TRI", str(d)[:10], db_path=ctx.market_db_path))
        xs = [str(d) for d in dates]
        ypf = _cum(vals)
        y50 = _cum(n50)
        y500 = _cum(n500)
        has50 = any(v is not None for v in y50)
        has500 = any(v is not None for v in y500)
        cached = (xs, ypf, y50, y500, has50, has500)
        st.session_state[cache_key] = cached
    xs, ypf, y50, y500, has50, has500 = cached

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=ypf, mode="lines+markers", name="Direct Equity", yaxis="y1"))
    if has50:
        fig.add_trace(go.Scatter(x=xs, y=y50, mode="lines+markers", name="Nifty 50 TRI", yaxis="y2"))
    if has500:
        fig.add_trace(go.Scatter(x=xs, y=y500, mode="lines+markers", name="Nifty 500 TRI", yaxis="y2"))
    fig.update_layout(
        title="Direct Equity cumulative growth vs benchmark (monthly)",
        yaxis={"title": "Direct Equity cumulative return (%)", "side": "left"},
        yaxis2={"title": "Benchmark cumulative return (%)", "side": "right", "overlaying": "y"},
        height=380, legend={"orientation": "h"}, margin={"t": 50, "b": 40, "l": 55, "r": 55},
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, width="stretch")

def _compute_stock_attribution(ctx: AppContext, s, e, idx_lookup):
    from collections import defaultdict

    from accounting.fifo import get_fifo_lots
    from performance import equity_analytics as ea

    txs = ctx.txs
    holdings = ctx.holdings
    lots = get_fifo_lots(txs, None, as_of_date=e)
    agg = defaultdict(lambda: [0.0, 0.0])
    for lot in lots:
        agg[lot["isin"]][0] += float(lot["qty"])
        agg[lot["isin"]][1] += float(lot["cost"])
    sector_map = {}
    if holdings is not None and not getattr(holdings, "empty", True) and "isin" in holdings.columns:
        for _, rr in holdings.iterrows():
            sector_map[str(rr.get("isin") or "").strip().upper()] = rr.get("sector")

    def _cmp(sid):
        try:
            p = ctx.pl(sid, e)
        except Exception:  # noqa: BLE001
            return None
        return float(p) if p is not None else None

    current_isins = [i for i, v in agg.items() if v[0] > 1e-6]
    total_mv = 0.0
    cur = {}
    for sid in current_isins:
        q = agg[sid][0]
        cmp = _cmp(sid)
        cv = (q * cmp) if cmp is not None else None
        cur[sid] = (q, cmp, cv)
        if cv:
            total_mv += cv

    current_rows = []
    for sid in sorted(current_isins):
        q, cmp, cv = cur[sid]
        cost = agg[sid][1]
        avg = (cost / q) if q else None
        daychg = ea.day_change_pct(ctx.pl, sid, e)
        sx = ea.stock_xirr(txs, ctx.dividend_flows, sid, s, e, terminal_value=cv, terminal_date=e)
        stwr = ea.stock_twr(txs, ctx.pl, sid, s, e)
        flows = ea.stock_flows(txs, ctx.dividend_flows, sid, s, e)
        bx = ea.benchmark_xirr_for_flows(flows, idx_lookup, s, e)
        al = ea.alpha(sx, bx)
        weight = (cv / total_mv * 100.0) if (cv and total_mv > 0) else None
        current_rows.append({
            "Stock": security_name(sid, txs, holdings),
            "Sector": sector_map.get(sid) or "",
            "Qty": round(q, 2),
            "Avg Buy": round(avg, 2) if avg is not None else None,
            "CMP": round(cmp, 2) if cmp is not None else None,
            "Day Chg %": (None if daychg is None else round(daychg * 100.0, 2)),
            "Invested": round(cost, 2),
            "Current": round(cv, 2) if cv is not None else None,
            "Weight %": (None if weight is None else round(weight, 2)),
            "Flag": ("high" if (weight is not None and weight > 15) else ""),
            "Stock XIRR %": (None if sx is None else round(sx * 100.0, 2)),
            "Stock TWR %": (None if stwr is None else round(stwr * 100.0, 2)),
            "Alpha %": (None if al is None else round(al * 100.0, 2)),
        })

    sold_isins = set()
    for t in txs or []:
        if str(t.get("buy_sell") or "").strip().upper() == "SELL":
            sid = str(t.get("isin") or "").strip().upper()
            if sid:
                sold_isins.add(sid)
    exited_isins = [sid for sid in sold_isins if agg.get(sid, [0.0, 0.0])[0] <= 1e-6]

    exited_rows = []
    for sid in sorted(exited_isins):
        buys = []
        sells = []
        for t in txs or []:
            if str(t.get("isin") or "").strip().upper() != sid:
                continue
            try:
                dd = ea._to_date(ea._tx_date(t))
            except (TypeError, ValueError):
                continue
            if dd < s or dd > e:
                continue
            amt = abs(float(t.get("net_amount") or 0.0))
            side = str(t.get("buy_sell") or "").strip().upper()
            if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
                buys.append((dd, amt))
            elif side == "SELL":
                sells.append((dd, amt))
        if not buys or not sells:
            continue
        first_buy = min(d for d, _ in buys)
        final_sell = max(d for d, _ in sells)
        if final_sell < first_buy:
            continue
        days = (final_sell - first_buy).days
        deployed = sum(a for _, a in buys)
        exit_val = sum(a for _, a in sells)
        divs = 0.0
        for d in ctx.dividend_flows or []:
            if str(d.get("security_id") or d.get("isin") or "").strip().upper() != sid:
                continue
            try:
                dd = ea._to_date(d.get("payment_date") or d.get("ex_date") or d.get("date"))
            except (TypeError, ValueError):
                continue
            if first_buy <= dd <= final_sell:
                divs += abs(float(d.get("gross") or d.get("amount") or 0.0))
        exit_total = exit_val + divs
        pnl = exit_total - deployed
        pnl_pct = (pnl / deployed * 100.0) if deployed else None
        rx = ea.stock_xirr(txs, ctx.dividend_flows, sid, first_buy, final_sell)
        rtwr = ea.stock_twr(txs, ctx.pl, sid, first_buy, final_sell)
        flows = ea.stock_flows(txs, ctx.dividend_flows, sid, first_buy, final_sell)
        bx = ea.benchmark_xirr_for_flows(flows, idx_lookup, first_buy, final_sell)
        al = ea.alpha(rx, bx)
        exited_rows.append({
            "Stock": security_name(sid, txs, holdings),
            "Sector": sector_map.get(sid) or "",
            "Holding Period": str(first_buy) + " to " + str(final_sell) + " (" + str(days) + "d)",
            "Deployed": round(deployed, 2),
            "Exit Value": round(exit_total, 2),
            "P&L": round(pnl, 2),
            "P&L %": (None if pnl_pct is None else round(pnl_pct, 2)),
            "Realized XIRR %": (None if rx is None else round(rx * 100.0, 2)),
            "Realized TWR %": (None if rtwr is None else round(rtwr * 100.0, 2)),
            "Alpha %": (None if al is None else round(al * 100.0, 2)),
        })

    return {"current": current_rows, "exited": exited_rows}
def _render_stock_attribution(ctx: AppContext) -> None:
    from datetime import date as _d

    import plotly.graph_objects as go

    from app.pages.portfolio import _period_bounds, _year_options
    from market_data.prices import get_index_tri
    from performance import equity_analytics as ea

    txs = ctx.txs
    f = ctx.filters
    st.markdown("#### Performance Visualization & Stock Attribution")

    opts = _year_options(txs, [], f)
    period_sel = st.session_state.get("eq_period", "Since inception")
    if period_sel not in opts:
        period_sel = "Since inception"
    p_start, p_end = _period_bounds(f, period_sel)
    valid = p_start is not None and p_end is not None and p_start <= p_end
    if not valid or not txs:
        st.caption("No equity data for the selected period.")
        return
    s = p_start if isinstance(p_start, _d) else _d.fromisoformat(str(p_start)[:10])
    e = p_end if isinstance(p_end, _d) else _d.fromisoformat(str(p_end)[:10])

    bench_map = {"Nifty 50": "NIFTY50TRI", "Nifty 500": "NIFTY500TRI"}
    bench_label = st.session_state.get("eq_benchmark", "Nifty 50")
    bench_id = bench_map.get(bench_label, "NIFTY50TRI")

    def _idx(d):
        try:
            return get_index_tri(bench_id, str(d)[:10], db_path=ctx.market_db_path)
        except Exception:  # noqa: BLE001
            return None

    st.caption("Following the Period and Benchmark selectors above. Period: " + str(s)[:10] + " to " + str(e)[:10] + " | Benchmark: " + bench_label + ".")

    viz_key = ("sec2_viz", tx_hash(txs), str(s)[:10], str(e)[:10], bench_id, getattr(f, "person_id", None), len(ctx.opening_positions or []))
    viz = st.session_state.get(viz_key)
    if viz is None:
        with st.spinner("Computing portfolio vs benchmark and calendar-year returns..."):
            port = ea.twr_index_series(txs, ctx.pl, s, e, ctx.opening_positions, base=100.0)
            def _ix50(dd):
                try:
                    return get_index_tri("NIFTY50TRI", str(dd)[:10], db_path=ctx.market_db_path)
                except Exception:  # noqa: BLE001
                    return None
            def _ix500(dd):
                try:
                    return get_index_tri("NIFTY500TRI", str(dd)[:10], db_path=ctx.market_db_path)
                except Exception:  # noqa: BLE001
                    return None
            n50 = ea.benchmark_index_series(_ix50, s, e, base=100.0)
            n500 = ea.benchmark_index_series(_ix500, s, e, base=100.0)
            cal = []
            for yy in range(s.year, e.year + 1):
                ys = max(_d(yy, 1, 1), s)
                ye = min(_d(yy, 12, 31), e)
                if ys > ye:
                    continue
                px = ea.portfolio_xirr(txs, ctx.corporate_actions, ctx.dividend_flows, ctx.pl, ys, ye, ctx.opening_positions)
                flows = ea.portfolio_flows(txs, ctx.dividend_flows, ys, ye)
                bx = ea.benchmark_xirr_for_flows(flows, _idx, ys, ye)
                cal.append({"year": str(yy), "portfolio": px, "benchmark": bx})
        viz = {"port": port, "n50": n50, "n500": n500, "cal": cal}
        st.session_state[viz_key] = viz

    port = viz["port"]
    n50 = viz["n50"]
    n500 = viz["n500"]
    cal = viz["cal"]

    viz_tabs = st.tabs(["Cumulative Growth vs Benchmark", "Calendar Year Returns"])
    with viz_tabs[0]:
        if not port:
            st.caption("No data for this period.")
        else:
            x = [p["date"] for p in port]
            py = [p["nav"] for p in port]
            m50 = {b["date"]: b["index"] for b in n50}
            m500 = {b["date"]: b["index"] for b in n500}
            y50 = [m50.get(dd) for dd in x]
            y500 = [m500.get(dd) for dd in x]
            msel = m50 if bench_id == "NIFTY50TRI" else m500
            ysel = [msel.get(dd) for dd in x]
            pct = [None if v is None else (v - 100.0) for v in py]
            spct = [None if v is None else (v - 100.0) for v in ysel]
            alp = [None if (a is None or b is None) else a - b for a, b in zip(pct, spct)]
            cdata = [[(None if pct[i] is None else round(pct[i], 2)), (None if alp[i] is None else round(alp[i], 2))] for i in range(len(x))]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=x, y=py, mode="lines", name="Direct Equity (base 100)", customdata=cdata, hovertemplate="%{x}<br>Direct Equity %{y:.1f} | ret %{customdata[0]:+.1f}% | alpha %{customdata[1]:+.1f}%<extra></extra>"))
            if any(v is not None for v in y50):
                fig.add_trace(go.Scatter(x=x, y=y50, mode="lines", name="Nifty 50 (base 100)", yaxis="y2", hovertemplate="%{x}<br>Nifty 50 %{y:.1f}<extra></extra>"))
            if any(v is not None for v in y500):
                fig.add_trace(go.Scatter(x=x, y=y500, mode="lines", name="Nifty 500 (base 100)", yaxis="y2", hovertemplate="%{x}<br>Nifty 500 %{y:.1f}<extra></extra>"))
            fig.update_layout(title="Cumulative growth vs benchmark (rebased to 100)", height=400, yaxis={"title": "Direct Equity (base 100)", "side": "left"}, yaxis2={"title": "Benchmarks (base 100)", "side": "right", "overlaying": "y"}, legend={"orientation": "h"}, margin={"t": 50, "b": 40, "l": 55, "r": 55}, hovermode="x unified", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch")
            st.caption("Rebased to 100 at period start. Both Nifty 50 and Nifty 500 TRI shown. Hover portfolio for exact % return and Alpha vs the selected benchmark.")
    with viz_tabs[1]:
        if not cal:
            st.caption("No calendar-year data.")
        else:
            years = [c["year"] for c in cal]
            pvals = [None if c["portfolio"] is None else round(c["portfolio"] * 100.0, 2) for c in cal]
            bvals = [None if c["benchmark"] is None else round(c["benchmark"] * 100.0, 2) for c in cal]
            figb = go.Figure()
            figb.add_trace(go.Bar(x=years, y=pvals, name="Direct Equity XIRR", hovertemplate="%{x}<br>Portfolio %{y:.1f}%<extra></extra>"))
            figb.add_trace(go.Bar(x=years, y=bvals, name=bench_label + " (cash-flow matched)", hovertemplate="%{x}<br>" + bench_label + " %{y:.1f}%<extra></extra>"))
            figb.update_layout(barmode="group", title="Calendar-year return: portfolio vs " + bench_label, height=400, yaxis_title="Return % p.a.", xaxis_title="Year", legend={"orientation": "h"}, margin={"t": 50, "b": 40, "l": 55, "r": 20}, plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(figb, width="stretch")
            st.caption("Portfolio = XIRR per calendar year (mark-to-market at year end); Benchmark = identical cash-flows simulated into the index.")

    st.markdown("##### Stock-level Return Attribution")
    stock_key = ("sec2_stocks", tx_hash(txs), str(s)[:10], str(e)[:10], bench_id, getattr(f, "person_id", None), len(ctx.opening_positions or []))
    stocks = st.session_state.get(stock_key)
    if stocks is None:
        with st.spinner("Computing stock-level XIRR / TWR and benchmark alpha..."):
            stocks = _compute_stock_attribution(ctx, s, e, _idx)
        st.session_state[stock_key] = stocks

    stab = st.tabs(["Current Holdings", "Sold-Off Positions"])
    with stab[0]:
        ui.safe_dataframe(pd.DataFrame(stocks["current"]), empty_message="No current holdings for this period.")
    with stab[1]:
        ui.safe_dataframe(pd.DataFrame(stocks["exited"]), empty_message="No sold-off positions for this period.")


def _render_whatif_simulator(ctx: AppContext) -> None:
    from datetime import date as _d

    import plotly.graph_objects as go

    from market_data.prices import get_index_tri
    from performance import equity_analytics as ea

    txs = ctx.txs
    f = ctx.filters
    md = ctx.market_db_path
    ui.section("What-If & Backtesting Simulator", "Test a past decision against an alternative, or backtest two tickers head-to-head. Nothing is executed.")
    if not txs:
        st.info("No equity transactions available.")
        return

    smap = ea.build_symbol_map(md)
    isym = ea.isin_symbol_map(md)

    def _px_for(query):
        q = str(query or "").strip().upper()
        if q in ("NIFTY50", "NIFTY 50", "NIFTY50TRI"):
            return (lambda d: get_index_tri("NIFTY50TRI", str(d)[:10], db_path=md)), "Nifty 50"
        if q in ("NIFTY500", "NIFTY 500", "NIFTY500TRI"):
            return (lambda d: get_index_tri("NIFTY500TRI", str(d)[:10], db_path=md)), "Nifty 500"
        isin = ea.resolve_security(q, smap)
        if not isin:
            return None, None
        return (lambda d: ctx.pl(isin, d)), (isym.get(isin, q) or isin)

    def _px_db_direct(sid):
        from market_data.prices import get_stock_price

        def _f(d):
            try:
                return get_stock_price(sid, str(d)[:10], db_path=md, allow_last=True)
            except Exception:  # noqa: BLE001
                return None

        return _f

    def _px_fresh(query):
        q = str(query or "").strip().upper()
        if q in ("NIFTY50", "NIFTY 50", "NIFTY50TRI"):
            return (lambda d: get_index_tri("NIFTY50TRI", str(d)[:10], db_path=md)), "Nifty 50"
        if q in ("NIFTY500", "NIFTY 500", "NIFTY500TRI"):
            return (lambda d: get_index_tri("NIFTY500TRI", str(d)[:10], db_path=md)), "Nifty 500"
        isin = ea.resolve_security(q, smap)
        if not isin:
            return None, None
        return _px_db_direct(isin), (isym.get(isin, q) or isin)

    def _yahoo_symbol_for(isin):
        try:
            import json
            import urllib.parse
            import urllib.request

            url = "https://query1.finance.yahoo.com/v1/finance/search?q=" + urllib.parse.quote(str(isin)) + "&quotesCount=5"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            for quote in data.get("quotes", []) or []:
                sym = quote.get("symbol")
                if sym:
                    return str(sym)
        except Exception:  # noqa: BLE001
            return None
        return None

    def _import_price_history(isin, start, end, symbol_query=None):
        from market_data.prices import fetch_stock_prices, resolve_provider_symbol
        from market_data.repository import MarketDataStore

        sid = str(isin or "").strip().upper()
        if not sid:
            return {"ok": False, "fetched": 0, "key": sid, "error": "no ticker key for import"}
        is_isin = (sid.startswith("IN") and len(sid) == 12) or sid.startswith("INF")
        sym = None
        try:
            sym = resolve_provider_symbol(sid, db_path=md)
        except Exception:  # noqa: BLE001
            sym = None
        if not sym:
            sym = _yahoo_symbol_for(symbol_query or sid)
            if sym and is_isin:
                try:
                    MarketDataStore(md).upsert_security_symbol_map([{
                        "security_id": sid,
                        "symbol": sym,
                        "provider_symbol": sym,
                        "exchange": "NSE",
                        "provider": "yfinance",
                        "source": "auto-import",
                        "verification_status": "unverified",
                    }])
                except Exception:  # noqa: BLE001, S110
                    pass
        if not sym:
            return {"ok": False, "fetched": 0, "key": sid, "error": "could not determine a market symbol for " + sid}
        try:
            from datetime import timedelta as _td

            _fetch_start = (_d.fromisoformat(str(start)[:10]) - _td(days=15)).isoformat()
        except Exception:  # noqa: BLE001
            _fetch_start = str(start)[:10]
        res = fetch_stock_prices(sid, start=_fetch_start, end=str(end)[:10], db_path=md, symbol=sym, persist=True)
        n = int(res.get("rows_fetched") or 0)
        errs = res.get("errors") or []
        return {"ok": n > 0, "fetched": n, "symbol": sym, "key": sid, "error": (None if n > 0 else ("; ".join(str(e) for e in errs) or "no rows returned"))}

    def _isin_of(t):
        return str(t.get("isin") or t.get("security") or "").strip().upper()

    tabs = st.tabs(["Mode A: What If I Bought Stock B Instead?", "Mode B: Stock vs Stock Backtester"])
    with tabs[0]:
        st.caption("Replicate your exact buy/sell dates and rupee amounts in an alternative stock or ETF.")
        secs = ea.get_portfolio_securities(txs, isym)
        if not secs:
            st.info("No holdings securities found.")
        else:
            labels = [s["label"] for s in secs]
            c1, c2 = st.columns(2)
            with c1:
                pick = st.selectbox("Your stock", labels, key="wi_a_stock")
            with c2:
                alt_q = st.text_input("Alternative ticker", value="NIFTYBEES", key="wi_a_alt", help="e.g. NIFTYBEES, HDFCBANK, RELIANCE, TCS, ICICIBANK")
            sel = secs[labels.index(pick)]["isin"]
            alt_px, alt_label = _px_for(alt_q)
            if st.button("Simulate what-if", key="wi_a_run"):
                if alt_px is None:
                    _q0 = str(alt_q or "").strip()
                    if not _q0:
                        st.error("Enter an alternative ticker (symbol or ISIN).")
                    else:
                        _stx0 = [t for t in txs if _isin_of(t) == sel]
                        _ds0 = []
                        for _t in _stx0:
                            _dv = _t.get("date") or _t.get("trade_date") or _t.get("settlement_date")
                            if not _dv:
                                continue
                            try:
                                _ds0.append(_d.fromisoformat(str(_dv)[:10]))
                            except Exception:  # noqa: BLE001, S112
                                continue
                        _s0 = min(_ds0) if _ds0 else (f.start_date or _d(2004, 1, 1))
                        _e0 = f.end_date or _d.today()
                        st.session_state["wi_a_missing"] = {"alt_q": alt_q, "alt_label": alt_q, "alt_isin": None, "sel": sel, "sel_label": isym.get(sel, sel), "name": pick, "start": str(_s0)[:10], "end": str(_e0)[:10]}
                        st.session_state.pop("wi_a_res", None)
                else:
                    alt_isin = ea.resolve_security(alt_q, smap)
                    if alt_isin and str(alt_isin).strip().upper() == str(sel).strip().upper():
                        st.error("Actual and alternative must be different stocks. Pick another ticker.")
                    else:
                        stx = [t for t in txs if _isin_of(t) == sel]
                        if not stx:
                            st.warning("No transactions found for the selected stock.")
                        else:
                            with st.spinner("Simulating cash-flow matched opportunity cost..."):
                                res = ea.simulate_cashflow_matched(stx, lambda d: ctx.pl(sel, d), alt_px, f.end_date)
                            if res is None:
                                st.error("Simulation returned no results. Check price data availability.")
                            else:
                                _alt = res["alternative"]
                                _cur_sig = (str(sel), str(alt_q).strip().upper())
                                if _alt["deployed"] > 0 and _alt["units"] <= 1e-9:
                                    _tx_ds = []
                                    for _t in stx:
                                        _dv = _t.get("date") or _t.get("trade_date") or _t.get("settlement_date")
                                        if not _dv:
                                            continue
                                        try:
                                            _tx_ds.append(_d.fromisoformat(str(_dv)[:10]))
                                        except Exception:  # noqa: BLE001, S112
                                            continue
                                    _imp_start = min(_tx_ds) if _tx_ds else (f.start_date or _d(2004, 1, 1))
                                    _imp_end = f.end_date or _d.today()
                                    st.session_state["wi_a_missing"] = {"alt_q": alt_q, "alt_label": alt_label, "alt_isin": ea.resolve_security(alt_q, smap), "sel": sel, "sel_label": isym.get(sel, sel), "name": pick, "start": str(_imp_start)[:10], "end": str(_imp_end)[:10]}
                                    st.session_state.pop("wi_a_res", None)
                                else:
                                    st.session_state.pop("wi_a_missing", None)
                                    if _alt["deployed"] > 0 and _alt["value_end"] is None:
                                        st.warning(
                                            "Alternative '" + str(alt_label) + "' has no terminal price near " + str(f.end_date)[:10] +
                                            "; only realized proceeds are shown for it."
                                        )
                                    st.session_state["wi_a_res"] = {"res": res, "actual_label": isym.get(sel, sel), "alt_label": alt_label, "name": pick, "sig": _cur_sig}
            _amiss = st.session_state.get("wi_a_missing")
            if _amiss and not st.session_state.get("wi_a_res"):
                st.warning("Price history is missing for the alternative '" + str(_amiss["alt_label"]) + "' over your transaction window (" + str(_amiss["start"]) + " to " + str(_amiss["end"]) + "). Import it below to run the simulation.")
                if st.button("Import price history & run simulation", key="wi_a_import", type="primary"):
                    _key = _amiss.get("alt_isin") or str(_amiss.get("alt_q") or "").strip().upper()
                    with st.spinner("Fetching price history for " + str(_amiss["alt_label"]) + "..."):
                        _imp = _import_price_history(_key, _amiss["start"], _amiss["end"], symbol_query=_amiss.get("alt_q"))
                    if not _imp["ok"]:
                        st.error("Could not import price history for " + str(_amiss["alt_label"]) + ": " + str(_imp.get("error") or "unknown error") + ". You can add a symbol mapping in Data & Settings, then retry.")
                    else:
                        st.info("Imported " + str(_imp["fetched"]) + " rows for " + str(_amiss["alt_label"]) + " (" + str(_imp.get("symbol") or _key) + ").")
                        _alt_px2 = _px_db_direct(_imp.get("key") or _key)
                        _stx2 = [t for t in txs if _isin_of(t) == _amiss["sel"]]
                        with st.spinner("Re-running simulation..."):
                            _res2 = ea.simulate_cashflow_matched(_stx2, lambda d: ctx.pl(_amiss["sel"], d), _alt_px2, f.end_date)
                        if _res2 is None:
                            st.error("Simulation still returned no results after import.")
                        else:
                            _alt2 = _res2["alternative"]
                            if _alt2["deployed"] > 0 and _alt2["units"] <= 1e-9:
                                st.error("Alternative '" + str(_amiss["alt_label"]) + "' still has no usable price data after import; the provider may lack data for these dates.")
                            else:
                                st.session_state["wi_a_res"] = {"res": _res2, "actual_label": _amiss["sel_label"], "alt_label": _amiss["alt_label"], "name": _amiss["name"], "sig": (str(_amiss["sel"]), str(_amiss["alt_q"]).strip().upper())}
                                st.session_state.pop("wi_a_missing", None)
                                st.success("Price history imported. Showing simulation results below.")
            wi = st.session_state.get("wi_a_res")
            _sig = (str(sel), str(alt_q).strip().upper())
            if wi and wi.get("res") and wi.get("sig") != _sig:
                st.info("Selection changed since the last run \u2014 click 'Simulate what-if' to refresh the results for this ticker.")
                wi = None
            if wi and wi.get("res"):
                res = wi["res"]
                a = res["actual"]
                b = res["alternative"]
                cm1, cm2 = st.columns(2)
                for col, lbl, m in ((cm1, "Actual: " + wi["actual_label"], a), (cm2, "Alternative: " + wi["alt_label"], b)):
                    with col:
                        st.markdown("**" + lbl + "**")
                        st.metric("Total Invested", inr(m["deployed"]))
                        _ve = m["value_end"]
                        _ve_disp = inr(_ve) if _ve is not None else "N/A"
                        st.metric("Final Value (Held)", _ve_disp)
                        st.metric("Realized (Sold)", inr(m["realized"]))
                        if m["deployed"] > 0 and _ve is None:
                            st.caption("\u26a0 Price data unavailable for this ticker near the end date, so holding value can't be computed.")
                        elif m["deployed"] > 0 and _ve <= 0:
                            if m["realized"] > 0:
                                st.caption("\u26a0 No current holding (position fully sold by end date). Realized (Sold) shows exit proceeds.")
                            else:
                                st.caption("\u26a0 No units bought (no usable prices on transaction dates) and nothing realized.")
                        st.metric("P&L (Realized+Unrealized)", inr(m["total_pnl"]))
                        st.metric("P&L %", fmt_pct(m["pnl_pct"]) if m["pnl_pct"] is not None else "N/A")
                        st.metric("XIRR", fmt_pct(m["xirr"]) if m["xirr"] is not None else "N/A")
                        st.metric("Max Drawdown", fmt_pct(m["max_drawdown"]))
                diff = (a["total_pnl"] or 0.0) - (b["total_pnl"] or 0.0)
                if diff >= 0:
                    st.success("You gained +" + inr(diff) + " more by choosing " + wi["actual_label"] + " over " + wi["alt_label"] + ".")
                else:
                    st.warning("You lost " + inr(-diff) + " by choosing " + wi["actual_label"] + " instead of " + wi["alt_label"] + ".")
                aser = a["series"]; bser = b["series"]
                bmap = {d: v for d, v in bser}
                xs = [d for d, _ in aser]
                ya = [v for _, v in aser]
                yb = [bmap.get(d) for d in xs]
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=xs, y=ya, mode="lines", name="Actual: " + wi["actual_label"]))
                fig.add_trace(go.Scatter(x=xs, y=yb, mode="lines", name="Alternative: " + wi["alt_label"]))
                fig.update_layout(title="Position value over holding timeline", yaxis_title="Value (INR)", height=380, legend={"orientation": "h"}, margin={"t": 50, "b": 40, "l": 55, "r": 20}, plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig, width="stretch")
    with tabs[1]:
        st.caption("Backtest two tickers head-to-head (stocks or Nifty 50/500) over a custom window.")
        base_bench = st.session_state.get("eq_benchmark", "Nifty 50")
        c1, c2 = st.columns(2)
        with c1:
            tq_a = st.text_input("Ticker A", value="RELIANCE", key="wi_b_a", help="symbol or ISIN, e.g. RELIANCE, HDFCBANK, TCS, NIFTY50")
        with c2:
            tq_b = st.text_input("Ticker B", value=base_bench, key="wi_b_b", help="symbol, ISIN, or NIFTY50/NIFTY500")
        pc1, pc2, pc3, pc4 = st.columns(4)
        today = _d.today()
        if st.session_state.get("wi_b_preset") is None:
            st.session_state["wi_b_preset"] = "5Y"

        def _preset_start(p, base_end):
            if p == "Max":
                return f.start_date or _d(2004, 1, 1)
            yrs = int(p[0])
            return _d(base_end.year - yrs, base_end.month, min(base_end.day, 28))

        _clicked = None
        if pc1.button("1Y", key="wi_p1"): _clicked = "1Y"
        if pc2.button("3Y", key="wi_p3"): _clicked = "3Y"
        if pc3.button("5Y", key="wi_p5"): _clicked = "5Y"
        if pc4.button("Max", key="wi_pmax"): _clicked = "Max"
        if _clicked:
            st.session_state["wi_b_preset"] = _clicked

        preset = st.session_state.get("wi_b_preset", "5Y")
        _base_end = f.end_date or today
        # A preset button was pressed: push the recomputed window into the date widgets.
        # (st.date_input ignores value= once its key is in session_state, so we must sync here.)
        if _clicked:
            st.session_state["wi_b_end"] = _base_end
            st.session_state["wi_b_start"] = _preset_start(preset, _base_end)
        if "wi_b_end" not in st.session_state:
            st.session_state["wi_b_end"] = _base_end
        if "wi_b_start" not in st.session_state:
            st.session_state["wi_b_start"] = _preset_start(preset, st.session_state["wi_b_end"])

        dc1, dc2 = st.columns(2)
        with dc1:
            d_start = st.date_input("Start date", key="wi_b_start")
        with dc2:
            d_end = st.date_input("End date", key="wi_b_end")
        sc1, sc2 = st.columns(2)
        with sc1:
            strat = st.radio("Strategy", ["Lump Sum", "Monthly SIP"], horizontal=True, key="wi_b_strat")
        with sc2:
            default_amt = 100000.0 if strat == "Lump Sum" else 10000.0
            amt = st.number_input("Amount (INR)", min_value=1000.0, value=default_amt, step=5000.0, key="wi_b_amt")
        if st.button("Run backtest", key="wi_b_run"):
            px_a, lab_a = _px_for(tq_a)
            px_b, lab_b = _px_for(tq_b)
            if px_a is None or px_b is None:
                _bad = [t for t, px in ((tq_a, px_a), (tq_b, px_b)) if px is None]
                st.error("Could not resolve ticker(s): " + ", ".join(str(b) for b in _bad) + ". Use a symbol (e.g. RELIANCE), an ISIN, or NIFTY50/NIFTY500.")
            elif d_start >= d_end:
                st.error("Start date (" + str(d_start) + ") must be before end date (" + str(d_end) + "). Adjust the dates or pick a preset.")
            else:
                with st.spinner("Running backtest..."):
                    if strat == "Lump Sum":
                        ra = ea.simulate_lump_sum(amt, px_a, d_start, d_end)
                        rb = ea.simulate_lump_sum(amt, px_b, d_start, d_end)
                    else:
                        ra = ea.simulate_sip(amt, px_a, d_start, d_end)
                        rb = ea.simulate_sip(amt, px_b, d_start, d_end)
                    cy_a = ea.calendar_year_returns(px_a, d_start, d_end)
                    cy_b = ea.calendar_year_returns(px_b, d_start, d_end)
                if ra is None or rb is None:
                    _fails = []
                    if ra is None:
                        _fails.append((tq_a, lab_a, ea.resolve_security(tq_a, smap)))
                    if rb is None:
                        _fails.append((tq_b, lab_b, ea.resolve_security(tq_b, smap)))
                    st.session_state["wi_b_missing"] = {"fails": _fails, "tq_a": tq_a, "tq_b": tq_b, "lab_a": lab_a, "lab_b": lab_b, "strat": strat, "amt": amt, "start": str(d_start), "end": str(d_end)}
                    st.session_state.pop("wi_b_res", None)
                else:
                    st.session_state.pop("wi_b_missing", None)
                    st.session_state["wi_b_res"] = {"a": ra, "b": rb, "la": lab_a, "lb": lab_b, "cya": cy_a, "cyb": cy_b, "strat": strat, "start": str(d_start), "end": str(d_end)}
        _miss = st.session_state.get("wi_b_missing")
        if _miss and not st.session_state.get("wi_b_res"):
            _labels = ", ".join(str(f[1]) for f in _miss["fails"])
            st.warning("Price history is missing for " + _labels + " over " + str(_miss["start"]) + " to " + str(_miss["end"]) + ". Import it below to run the backtest.")
            if st.button("Import price history & run backtest", key="wi_b_import_all", type="primary"):
                _logs = []
                _all_ok = True
                for _tq, _lab, _isin in _miss["fails"]:
                    with st.spinner("Fetching price history for " + str(_lab) + "..."):
                        _imp = _import_price_history(_isin, _miss["start"], _miss["end"])
                    if _imp["ok"]:
                        _logs.append("Imported " + str(_imp["fetched"]) + " rows for " + str(_lab) + " (" + str(_imp.get("symbol") or _isin) + ")")
                    else:
                        _all_ok = False
                        _logs.append("Could not import " + str(_lab) + ": " + str(_imp.get("error") or "unknown error"))
                for _l in _logs:
                    st.info(_l)
                if _all_ok:
                    _pxa2, _la2 = _px_fresh(_miss["tq_a"])
                    _pxb2, _lb2 = _px_fresh(_miss["tq_b"])
                    _s = _d.fromisoformat(str(_miss["start"]))
                    _e = _d.fromisoformat(str(_miss["end"]))
                    if _pxa2 is None or _pxb2 is None:
                        st.error("Could not resolve one of the tickers after import.")
                    else:
                        if _miss["strat"] == "Lump Sum":
                            _ra = ea.simulate_lump_sum(_miss["amt"], _pxa2, _s, _e)
                            _rb = ea.simulate_lump_sum(_miss["amt"], _pxb2, _s, _e)
                        else:
                            _ra = ea.simulate_sip(_miss["amt"], _pxa2, _s, _e)
                            _rb = ea.simulate_sip(_miss["amt"], _pxb2, _s, _e)
                        _cya = ea.calendar_year_returns(_pxa2, _s, _e)
                        _cyb = ea.calendar_year_returns(_pxb2, _s, _e)
                        if _ra is not None and _rb is not None:
                            st.session_state["wi_b_res"] = {"a": _ra, "b": _rb, "la": _la2, "lb": _lb2, "cya": _cya, "cyb": _cyb, "strat": _miss["strat"], "start": str(_miss["start"]), "end": str(_miss["end"])}
                            st.session_state.pop("wi_b_missing", None)
                            st.success("Price history imported. Showing backtest results below.")
                        else:
                            _still = []
                            if _ra is None:
                                _still.append((_miss["tq_a"], _la2, ea.resolve_security(_miss["tq_a"], smap)))
                            if _rb is None:
                                _still.append((_miss["tq_b"], _lb2, ea.resolve_security(_miss["tq_b"], smap)))
                            _miss["fails"] = _still
                            st.session_state["wi_b_missing"] = _miss
                            st.warning("Still missing price data for " + ", ".join(str(f[1]) for f in _still) + " after import; the provider may lack data for this range.")
        wb = st.session_state.get("wi_b_res")
        if wb and wb.get("a") and wb.get("b"):
            ra, rb = wb["a"], wb["b"]
            def _bw(cy):
                vals = [c["return"] for c in cy]
                if not vals:
                    return None, None, None
                best = max(cy, key=lambda c: c["return"])
                worst = min(cy, key=lambda c: c["return"])
                return best["year"], best["return"], worst
            def _bwret(cy):
                if not cy:
                    return None, None
                b = max(cy, key=lambda c: c["return"])
                w = min(cy, key=lambda c: c["return"])
                return (b["year"] + " (" + fmt_pct(b["return"]) + ")"), (w["year"] + " (" + fmt_pct(w["return"]) + ")")
            ba, wa = _bwret(wb["cya"])
            bb, wb2 = _bwret(wb["cyb"])
            def _fmt(v, pct=False):
                if v is None:
                    return "N/A"
                return fmt_pct(v) if pct else inr(v)
            rows = [
                {"Metric": "Total Invested", wb["la"]: _fmt(ra["invested"]), wb["lb"]: _fmt(rb["invested"])},
                {"Metric": "Final Value", wb["la"]: _fmt(ra["value_end"]), wb["lb"]: _fmt(rb["value_end"])},
                {"Metric": "Absolute Return", wb["la"]: _fmt(ra["abs_return"], True), wb["lb"]: _fmt(rb["abs_return"], True)},
                {"Metric": "XIRR / CAGR", wb["la"]: _fmt(ra["xirr"], True), wb["lb"]: _fmt(rb["xirr"], True)},
                {"Metric": "Max Drawdown", wb["la"]: _fmt(ra["max_drawdown"], True), wb["lb"]: _fmt(rb["max_drawdown"], True)},
                {"Metric": "Best Calendar Year", wb["la"]: (ba or "N/A"), wb["lb"]: (bb or "N/A")},
                {"Metric": "Worst Calendar Year", wb["la"]: (wa or "N/A"), wb["lb"]: (wb2 or "N/A")},
            ]
            ui.safe_dataframe(pd.DataFrame(rows))
            sa = ra["series"]; sb = rb["series"]
            bmap = {d: v for d, v in sb}
            xs = [d for d, _ in sa]
            ya = [v for _, v in sa]
            yv = [bmap.get(d) for d in xs]
            invest = [ra["invested"] for _ in xs]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=xs, y=invest, mode="lines", name="Invested", line={"dash": "dot"}))
            fig.add_trace(go.Scatter(x=xs, y=ya, mode="lines", name=wb["la"]))
            fig.add_trace(go.Scatter(x=xs, y=yv, mode="lines", name=wb["lb"]))
            fig.update_layout(title="Growth of capital: " + wb["la"] + " vs " + wb["lb"], yaxis_title="Value (INR)", height=400, legend={"orientation": "h"}, margin={"t": 50, "b": 40, "l": 55, "r": 20}, plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch")


def _inr_indian(n) -> str:
    """Format an integer with Indian digit grouping (e.g. 250000 -> '2,50,000')."""
    try:
        s = str(round(float(n)))
    except (TypeError, ValueError):
        return str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def _stage4_playbook_markdown(live: dict) -> str:
    """Build the full Strategy Playbook markdown with live-synced parameters."""
    trim_aer = live.get("trim_aer", 1.85)
    stop_mult = live.get("stop_mult", 1.5)
    min_mv = live.get("min_mv", 200000)
    bench = live.get("bench_xirr", 0.12)
    budget = live.get("budget", 300000)
    inc_max = live.get("inc_max", 100000)
    inc_days = live.get("inc_days", 180)
    inc_gtt = live.get("inc_gtt", 0.60)
    gtt_warn = live.get("gtt_warn", 0.10)
    max_weight_pct = live.get("max_weight_pct", 8.0)
    L = []
    L.append("# Strategy Playbook & Rulebook — Direct Equity")
    L.append("")
    L.append(f"_Live parameters: Profit-Trim AER {trim_aer:.2f}x · Vol Stop-Loss {stop_mult:.1f}x · "
             f"Min Trim Size ₹{_inr_indian(min_mv)} · Benchmark XIRR {bench * 100:.0f}% · "
             f"Monthly SIP Budget ₹{_inr_indian(budget)}_")
    L.append("")
    L.append("## 1. Core Philosophy & Monthly Cadence")
    L.append("Once-a-month review (~15 minutes) before deploying the monthly SIPs, using 100% "
             "mathematical formulas (zero manual stock bucketing) computed locally from daily "
             "Adjusted Close prices.")
    L.append("")
    L.append("## 2. New SIP Incubation Gate (Protecting the first SIPs)")
    L.append(f"Any stock with Invested Cost < ₹{_inr_indian(inc_max)} OR Holding Age < {inc_days} days stays "
             "in **Incubation Mode**.")
    L.append("- Locked at **1.0x** base SIP for rupee-cost averaging; profit trims & laggard exits are suppressed.")
    L.append(f"- Protected solely by a **{int((1 - inc_gtt) * 100)}% Catastrophe Circuit Breaker** "
             f"(Avg Buy Price × {inc_gtt:.2f}).")
    L.append("")
    L.append("## 3. Monthly SIP Deployment - 4-Lane Guide (Rs 5,000 units)")
    L.append("All-weather discipline (never chase vertical peaks). Each stock lands in one lane from its 200-DMA distance and AER:")
    L.append("- **Lane 1 (2.5x Resting Winner)** - 0% to +15% above 200-DMA, XIRR > Bench + 10%, AER < 1.30: double up on proven winners resting (cap Rs 35,000).")
    L.append("- **Lane 2 (1.0x Cruising / Incubating)** - 0% to +30% above 200-DMA, 0.85 <= AER < 1.35: steady accumulation (Incubating locked 1.0x).")
    L.append("- **Lane 3 (0.0x Overheated Rocket)** - more than +30% above 200-DMA OR AER >= 1.35: stop fresh money, keep shares compounding. Not a warning.")
    L.append("- **Lane 4 (0.0x Broken Trend)** - below 200-DMA OR AER < 0.85: freeze SIPs, verify GTT, exit 100% only on GTT or AER <= 0.50.")
    L.append("Every SIP is an exact Rs 5,000 multiple and capped at Rs 35,000 per stock; leftover parks in Liquid Fund / Dry Powder; sub-Rs 5,000 shows as Carry-Forward Cash.")
    L.append("## 4. The 4-Tier Sell & Profit-Booking Ladder (Priority Order)")
    L.append(f"**Priority 1 — Hard Volatility Stop-Loss (100% Exit):** Invested ≥ ₹1,50,000 AND "
             f"(CMP ≤ Avg Buy × (1 − StopLoss%) where StopLoss% = clamp({stop_mult:.1f}×AnnualVol, 20%, 45%), "
             "OR AER ≤ 0.50). Always exit 100% in one go (no partial stop-loss exits). The Auto GTT Stop-Loss "
             "Trigger = **max(Cost Floor, Trailing Peak Floor)**: once a winner is ≥ +100% (2.0x) over avg cost, the GTT "
             "ratchets up to Peak × (1 − clamp(1.5×AnnualVol, 35%, 45%)) to protect profits; otherwise it sits at "
             "the Cost Floor = Avg Buy × (1 − StopLoss%).")
    L.append("**Priority 2 — Trend-Break Profit Lock (50% Exit):** AER ≥ 1.30 AND CMP < 50-DMA × 0.95 "
             "(confirmed 5% break below 50-DMA) to lock in gains before a full reversal.")
    L.append(f"**Priority 3 — Concentration Trim (Reset to 1.40×):** AER ≥ {trim_aer:.2f} "
             f"(Market Weight > {trim_aer:.2f}× Cost Weight), Market Value ≥ ₹{_inr_indian(min_mv)}, and XIRR > 25%. "
             "Trim just enough shares (FIFO, LTCG > 365 days first, to minimize tax) to bring Market Weight "
             "back to 1.40× Cost Weight.")
    L.append("**Priority 4 — Opportunity-Cost Laggard Exit:** Flags non-incubating stocks held > 365 days "
             "in the Bottom 5 with Efficiency Score < 0 and CMP < 200-DMA for review & reallocation.")
    L.append("")
    L.append("## 5. Warning Level Protocol (3-Step Checklist)")
    L.append(f"Triggers when a stock is frozen at 0.0x by a **Trend Freeze** (CMP < 200-DMA or AER < 0.85) OR sits "
             f"within {int(gtt_warn * 100)}% of its GTT stop. The 0.0x **Over-Allocated Freeze** is NOT a warning "
             "(that is simply 'let winners run').")
    L.append("1. **Do Not Sell Existing Shares Yet** (avoid false-alarm whipsaws).")
    L.append("2. **Pause Fresh SIPs (0.0x)** and redirect capital to winners.")
    L.append("3. **Verify Active Broker GTT Order & 2-Minute Thesis Check** — exit 100% immediately only "
             "if there is a permanent governance, fraud, or severe financial/NBFC red flag.")
    L.append("")
    L.append("## 6. Laggard Exit (LAGGARD_EXIT) — 2-Minute Decision Filter")
    L.append("LAGGARD_EXIT is a **Priority 4 Review Flag**, not an automatic panic sell — eliminate dead money:")
    L.append("- **Hold (keep SIP at 0.0x):** Quarterly YoY Revenue/Profit growth still > 15% "
             "(business is compounding; valuation is just cooling off).")
    L.append("- **Exit 100% & Reallocate:** Quarterly YoY Revenue/Profit growth stalled < 10% for 2 consecutive quarters.")
    L.append("- **Tax-Loss Harvesting Bonus:** Prefer exiting stagnant laggards sitting at a loss during Feb–March "
             "to offset realized LTCG/STCG taxes from AER ≥ 1.50x profit trims.")
    L.append("")
    L.append("## 7. Staggered Cash Redeployment Playbook")
    L.append("Deploy cash freed up from HARD_STOP_LOSS, CONCENTRATION_TRIM, or LAGGARD_EXIT:")
    L.append("1. **No Lump-Sum Chasing:** Park net proceeds immediately in a Liquid Fund / LIQUIDBEES.")
    L.append("2. **Stagger Over 3–6 Monthly SIPs:** Divide the harvested cash pool into 3 equal monthly "
             "tranches (if < ₹3,00,000) or 6 tranches (if ≥ ₹3,00,000) and feed them into the Stage 4C "
             f"Monthly SIP Budget (₹{_inr_indian(budget)}) so 2.5x winners absorb capital gradually without "
             "distorting AER cost weights.")
    L.append("3. **New Replacement Exception:** Up to ₹50,000 starter deployment + ₹10,000 monthly SIPs "
             "until reaching the ₹1,00,000 incubation graduation mark.")
    L.append("")
    L.append("_Tax refs: LTCG 12.5% above ₹1.25L annual exemption; STCG 20%. Advisory only — verify with your CA._")
    return "\n".join(L)


def _render_playbook_panel(live: dict) -> None:
    """Render the scrollable playbook inside a popover/dialog with copy + download."""
    md = _stage4_playbook_markdown(live)
    st.download_button("📋 Copy Rulebook as Markdown", data=md.encode("utf-8"),
                       file_name="strategy_playbook.md", mime="text/markdown", key="s4d_copy_md")
    st.markdown(md)
    with st.expander("View raw Markdown (copy text)"):
        st.code(md, language="markdown")


def _render_stage4_action_center(ctx: AppContext) -> None:
    """Stage 4B - Sell / Profit-Booking / Stop-Loss Action Center (UI)."""
    from datetime import date as _d

    from performance import stage4_signals as s4

    txs = ctx.txs
    f = ctx.filters
    _hc1, _hc2 = st.columns([5, 2])
    with _hc1:
        st.markdown("#### Stage 4 - Sell / Profit-Booking / Stop-Loss Action Center")
        st.caption("Formula-driven action cards over local Adjusted-Close data. Advisory only - nothing is executed.")
    with _hc2:
        _live = {
            "trim_aer": float(st.session_state.get("s4_trim_aer", s4.TRIM_AER)),
            "stop_mult": float(st.session_state.get("s4_stop_mult", s4.STOP_LOSS_MULT)),
            "min_mv": float(st.session_state.get("s4_min_mv", s4.TRIM_MIN_MARKET_VALUE)),
            "bench_xirr": float(st.session_state.get("s4_bench_xirr", 0.12)),
            "budget": float(st.session_state.get("s4c_budget", s4.DEFAULT_MONTHLY_SIP_BUDGET)),
            "inc_max": float(s4.INCUBATION_MAX_INVESTED),
            "inc_days": int(s4.INCUBATION_MAX_DAYS),
            "inc_gtt": float(s4.INCUBATION_GTT_MULT),
            "gtt_warn": float(s4.GTT_BUFFER_WARN),
            "max_weight_pct": float(st.session_state.get("s4c_max_weight", 8.0)),
        }
        with st.popover("📖 Strategy Playbook & Rules", use_container_width=True):
            _render_playbook_panel(_live)
    if not txs:
        st.info("No equity transactions available.")
        return

    st.markdown("**Formula Controls**")
    cc = st.columns(4)
    with cc[0]:
        trim_aer = st.slider("Profit Trim AER Trigger", 1.20, 2.50, 1.85, 0.05, key="s4_trim_aer",
                             help="Trim when AER >= this; weight resets to 1.40x cost weight.")
    with cc[1]:
        stop_mult = st.slider("Volatility Stop-Loss x", 1.0, 3.0, 1.5, 0.1, key="s4_stop_mult",
                              help="Dynamic stop = clamp(mult x annual vol, 20%, 45%).")
    with cc[2]:
        min_mv = st.number_input("Min Position Size for Trim (Rs)", min_value=50000, max_value=5000000,
                                 value=200000, step=50000, key="s4_min_mv")
    with cc[3]:
        bench_xirr = st.slider("Benchmark XIRR", 0.0, 0.30, 0.12, 0.01, key="s4_bench_xirr")

    as_of = f.end_date or _d.today()
    px = s4.make_adjusted_close_lookup(ctx.market_db_path)

    active_isins = []
    try:
        if ctx.holdings is not None and not ctx.holdings.empty:
            for _, r in ctx.holdings.iterrows():
                if float(r.get("quantity") or 0) > 1e-6:
                    sid = str(r.get("isin") or "").strip().upper()
                    if sid:
                        active_isins.append(sid)
    except Exception:  # noqa: BLE001
        active_isins = []
    if not active_isins:
        st.info("No active equity holdings to evaluate.")
        return

    key = ("s4_rows", tx_hash(txs), str(as_of)[:10], getattr(f, "person_id", None),
           round(trim_aer, 3), round(stop_mult, 2), int(min_mv), round(bench_xirr, 3),
           len(ctx.opening_positions or []))
    cached = st.session_state.get(key)
    if cached is None:
        with st.spinner("Evaluating Stage 4 signals over Adjusted-Close history..."):
            stocks = []
            for sid in active_isins:
                try:
                    stocks.append(s4.build_stock_input(txs, px, as_of, sid,
                                                       dividends=ctx.dividend_flows,
                                                       opening_positions=ctx.opening_positions))
                except Exception:  # noqa: BLE001, S112
                    continue
            totals = s4.build_portfolio_totals(stocks)
            rows = s4.evaluate_signals(stocks, totals, bench_xirr, as_of_date=as_of,
                                       trim_aer=trim_aer, stop_loss_mult=stop_mult,
                                       min_market_value=float(min_mv))
            by_id = {s.security_id: s for s in stocks}
            for r in rows:
                s = by_id.get(r["security_id"])
                r["quantity"] = getattr(s, "quantity", 0.0)
                r["avg_buy_price"] = getattr(s, "avg_buy_price", 0.0)
                r["market_value"] = getattr(s, "market_value", None)
                r["invested_cost"] = getattr(s, "invested_cost", 0.0)
                r["xirr"] = getattr(s, "xirr", None)
        cached = {"rows": rows}
        st.session_state[key] = cached
    rows = cached["rows"]

    def _name(sid):
        try:
            return security_name(sid, txs, ctx.holdings) or sid
        except Exception:  # noqa: BLE001
            return sid

    # ---- prioritized action cards ----------------------------------------
    st.markdown("**Prioritized Sell & Trim Actions**")
    action_rows = [r for r in rows if r["signal_type"] in (
        s4.HARD_STOP_LOSS, s4.TREND_BREAK_LOCK, s4.CONCENTRATION_TRIM)]
    action_rows.sort(key=lambda r: r["priority"])
    if not action_rows:
        st.success("No sell / trim actions triggered under the current thresholds.")
    accents = {s4.HARD_STOP_LOSS: ("🔴", "#d64545"),
               s4.TREND_BREAK_LOCK: ("🟠", "#d98b00"),
               s4.CONCENTRATION_TRIM: ("🔵", "#1c7ed6")}
    for r in action_rows:
        sid = r["security_id"]
        sig = r["signal_type"]
        cmp_ = float(r.get("cmp") or 0.0)
        qty = float(r.get("quantity") or 0.0)
        aer = r.get("aer")
        stop = r.get("dynamic_stop_loss_pct")
        d200 = r.get("dma_200")
        emoji, color = accents.get(sig, ("•", "#555"))
        if sig == s4.HARD_STOP_LOSS:
            shares = round(qty) if qty > 0 else 0
            why = ("Volatility Stop-Loss breached" if "stop-loss" in (r.get("reason") or "")
                   else "AER collapsed to 0.50x or below")
            action = f"Exit 100% ({shares} shares, {inr(shares * cmp_)}) — {why}"
        elif sig == s4.TREND_BREAK_LOCK:
            shares = int(qty // 2)
            action = (f"Sell 50% ({shares} shares, {inr(shares * cmp_)}) — price broke below "
                      "50-DMA while AER >= 1.30")
        else:
            shares = int(r.get("shares_to_sell") or 0)
            from_w = (r.get("market_weight_pct") or 0.0) * 100.0
            to_w = (r.get("cost_weight_pct") or 0.0) * 100.0 * 1.40
            action = (f"Sell {shares} shares ({inr(shares * cmp_)}) to reset weight from "
                      f"{from_w:.1f}% to {to_w:.2f}%")
        with st.container(border=True):
            st.markdown(
                f"<span style='color:{color};font-weight:700;font-size:1.05rem'>"
                f"{emoji} {sig.replace('_', ' ')} — {_name(sid)}</span>",
                unsafe_allow_html=True,
            )
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("CMP", f"Rs {cmp_:,.1f}")
            mc2.metric("AER", f"{aer:.2f}x" if aer is not None else "N/A")
            mc3.metric("Dyn. Stop-Loss", f"{stop * 100:.1f}%" if stop is not None else "N/A")
            above = cmp_ > d200 if (cmp_ and d200) else None
            mc4.metric("200-DMA", (f"{'above' if above else 'below'} {d200:,.0f}" if d200 else "N/A"))
            _gtt = r.get("gtt_stop_loss_price")
            _anchor = r.get("gtt_anchor_type")
            _ap = r.get("gtt_anchor_price")
            if r.get("signal_type") == s4.HARD_STOP_LOSS:
                st.caption("Auto GTT Stop-Loss Trigger: Exit Now (Do Not Set GTT)")
            elif _gtt and _anchor:
                _ap_txt = f" from ₹{_ap:,.0f}" if _ap else ""
                st.caption(f"Auto GTT Stop-Loss Trigger: ₹{_gtt:,.1f} — {_anchor}{_ap_txt}")
            elif _gtt:
                st.caption(f"Auto GTT Stop-Loss Trigger: ₹{_gtt:,.1f}")
            st.markdown(f"**Action:** {action}")
            if shares > 0 and cmp_ > 0:
                taxp = s4.fifo_tax_preview(txs, sid, shares, cmp_, as_of,
                                           opening_positions=ctx.opening_positions)
                tc1, tc2, tc3, tc4 = st.columns(4)
                tc1.metric("LTCG / STCG shares",
                           f"{taxp['ltcg_shares']:.0f} / {taxp['stcg_shares']:.0f}")
                tc2.metric("Est. Realized Gain", inr(taxp["realized_gain"]))
                tc3.metric("Est. Tax", inr(taxp["estimated_tax"]))
                tc4.metric("Net Post-Tax Cash", inr(taxp["net_cash_unlocked"]))
                st.caption("FIFO estimate — LTCG 12.5% above ₹1.25L (annual, portfolio-wide), STCG 20%. Pre-import opening lots carry unknown cost (₹0).")
            st.caption(r.get("reason") or "")

    # ---- opportunity-cost laggards (Bottom 5) ----------------------------
    st.markdown("**Opportunity-Cost Laggards (Bottom 5)**")
    st.info("**2-Minute Laggard Filter:** Check YoY Earnings Growth — **>15% = Hold** (SIP paused at 0.0x)  |  "
            "**<10% for 2 quarters = Exit 100%** & Stagger Redeployment over 3–6 months.")

    def _is_laggard(r):
        eff = r.get("efficiency_score")
        cmp_ = r.get("cmp")
        d200 = r.get("dma_200")
        hs = r.get("holding_start_date")
        if eff is None or cmp_ is None or d200 is None or not hs:
            return False
        try:
            held = (as_of - _d.fromisoformat(str(hs)[:10])).days
        except Exception:  # noqa: BLE001
            return False
        return held > 365 and eff < 0 and cmp_ < d200

    laggards = sorted([r for r in rows if _is_laggard(r)],
                      key=lambda r: r["efficiency_score"])[:5]
    if not laggards:
        st.caption("No laggards flagged (need >365 days held, Efficiency < 0 and CMP < 200-DMA).")
    else:
        tbl = []
        for r in laggards:
            xirr = r.get("xirr")
            alpha = (xirr - bench_xirr) if xirr is not None else None
            tbl.append({
                "Stock": _name(r["security_id"]),
                "XIRR": fmt_pct(xirr) if xirr is not None else "N/A",
                "Alpha vs Bench": fmt_pct(alpha) if alpha is not None else "N/A",
                "AER": f"{r['aer']:.2f}x" if r.get("aer") is not None else "N/A",
                "Market Value": inr(r.get("market_value")),
            })
        st.dataframe(pd.DataFrame(tbl), hide_index=True, width="stretch")
        st.caption("Reallocation candidates: held > 1 year, Efficiency Score < 0 and price below 200-DMA.")


def _render_stage4_planner(ctx: AppContext) -> None:
    """Stage 4C - Monthly SIP Deployment, Warning Level & GTT Protection Planner."""
    from datetime import date as _d

    from performance import stage4_signals as s4

    txs = ctx.txs
    f = ctx.filters
    st.markdown("#### Stage 4C - Monthly SIP & GTT Protection Planner")
    st.caption("Plan this month's SIP deployment and verify broker GTT orders. Advisory only - nothing is executed.")
    if not txs:
        st.info("No equity transactions available.")
        return

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        budget = st.number_input("Total Monthly SIP Budget (Rs)", min_value=0, max_value=50000000,
                                 value=int(s4.DEFAULT_MONTHLY_SIP_BUDGET), step=25000, key="s4c_budget")
    with c2:
        include_harvest = st.checkbox("Include Harvested Cash (Stage 4B trims)", value=False,
                                      key="s4c_harvest")
    with c3:
        max_single_stock_sip = st.number_input("Max Single-Stock SIP (Rs)", min_value=0, max_value=500000, value=int(s4.MAX_SINGLE_STOCK_SIP), step=5000, key="s4c_max_sip", help="Cap each stock monthly SIP; leftover parks in Liquid Fund.")
    max_single_stock_sip = float(max_single_stock_sip)

    as_of = f.end_date or _d.today()
    px = s4.make_adjusted_close_lookup(ctx.market_db_path)

    active_isins = []
    try:
        if ctx.holdings is not None and not ctx.holdings.empty:
            for _, r in ctx.holdings.iterrows():
                if float(r.get("quantity") or 0) > 1e-6:
                    sid = str(r.get("isin") or "").strip().upper()
                    if sid:
                        active_isins.append(sid)
    except Exception:  # noqa: BLE001
        active_isins = []
    if not active_isins:
        st.info("No active equity holdings to plan for.")
        return

    key = ("s4c_rows", tx_hash(txs), str(as_of)[:10], getattr(f, "person_id", None),
           len(ctx.opening_positions or []), round(max_single_stock_sip, 4),
           len(st.session_state.get("s4c_incubation_entries", {})))
    cached = st.session_state.get(key)
    if cached is None:
        with st.spinner("Building this month's SIP plan over Adjusted-Close history..."):
            stocks = []
            for sid in active_isins:
                try:
                    stocks.append(s4.build_stock_input(txs, px, as_of, sid,
                                                       dividends=ctx.dividend_flows,
                                                       opening_positions=ctx.opening_positions))
                except Exception:  # noqa: BLE001, S112
                    continue
            totals = s4.build_portfolio_totals(stocks)
            rows = s4.evaluate_signals(stocks, totals, 0.12, as_of_date=as_of)
            by_id = {s.security_id: s for s in stocks}
            for r in rows:
                s = by_id.get(r["security_id"])
                r["quantity"] = getattr(s, "quantity", 0.0)
                r["market_value"] = getattr(s, "market_value", None)
                r["xirr"] = getattr(s, "xirr", None)
                r["avg_buy_price"] = getattr(s, "avg_buy_price", 0.0)
        cached = {"rows": rows}
        st.session_state[key] = cached
    rows = cached["rows"]

    def _name(sid):
        try:
            return security_name(sid, txs, ctx.holdings) or sid
        except Exception:  # noqa: BLE001
            return sid

    harvested = 0.0
    if include_harvest:
        for r in rows:
            _st = r.get("signal_type")
            if _st == s4.CONCENTRATION_TRIM:
                sh = int(r.get("shares_to_sell") or 0)
            elif _st == s4.HARD_STOP_LOSS:
                sh = int(r.get("quantity") or 0)
            else:
                sh = 0
            cmp_ = r.get("cmp") or 0
            if sh > 0 and cmp_ > 0:
                tp = s4.fifo_tax_preview(txs, r["security_id"], sh, cmp_, as_of,
                                         opening_positions=ctx.opening_positions)
                harvested += float(tp["net_cash_unlocked"])

    _inc_total_early = sum(float(e.get("starter_sip", 0.0)) for e in st.session_state.get("s4c_incubation_entries", {}).values())
    _effective_budget = max(0.0, float(budget) - _inc_total_early)
    alloc = s4.compute_monthly_sip_allocation(rows, _effective_budget, harvested_cash=harvested, max_single_stock_sip=max_single_stock_sip)
    arows = sorted(alloc["rows"], key=lambda r: -float(r.get("sip_multiplier") or 0))
    deploy = float(alloc["deployable_budget"])
    active_buy = sum(1 for r in arows if float(r.get("sip_multiplier") or 0) > 0)
    warn_count = sum(1 for r in arows if r.get("warning_level"))
    near_gtt = sum(1 for r in arows
                   if r.get("gtt_buffer_pct") is not None and r["gtt_buffer_pct"] <= s4.GTT_BUFFER_WARN)

    allocated = float(alloc.get("total_allocated", deploy)) + _inc_total_early
    parked = float(alloc.get("parked_cash", 0.0))
    k = st.columns(5)
    k[0].metric("Total Monthly Deployment (incl. Incubation)", inr(allocated))
    k[1].metric("Park in Liquid Fund / Dry Powder", inr(parked))
    k[2].metric("Active Buy Positions", str(active_buy))
    k[3].metric("Warning / Frozen (0.0x)", str(warn_count))
    k[4].metric("Near GTT Stop Alerts", str(near_gtt))
    if harvested > 0:
        st.caption("Includes " + inr(harvested) + " harvested cash from Stage 4B profit-booking.")
    if alloc.get("carry_forward_cash"):
        st.caption("Carry-Forward Cash (<₹5k): " + inr(alloc["carry_forward_cash"])
                   + " — below the ₹5,000 rounding unit; rolls into next month.")

    if alloc["all_frozen"]:
        st.warning("All holdings are frozen at 0.0x (broad downturn below 200-DMA). "
                   "Hold Cash / Park in Liquid Fund: " + inr(deploy) + " undeployed this month.")
    if warn_count > 0:
        st.info("**Warning Level — action required:** (1) Do NOT sell existing shares yet.  "
                "(2) Pause fresh SIPs (0.0x).  (3) Verify your active broker GTT order & check for "
                "governance / sector red flags.")

    # ---- Smart New-Stock Entry & Incubation Pacing (Stage 4C enhancement) ---------------------
    if "s4c_incubation_entries" not in st.session_state:
        st.session_state["s4c_incubation_entries"] = {}
    inc_entries: dict = st.session_state["s4c_incubation_entries"]

    st.markdown("**+ Simulate New Stock Entry** (incubation pacing planner)")
    _c1, _c2, _c3 = st.columns([3, 1, 1])
    with _c1:
        _new_tk = st.text_input("NSE Ticker", value="", key="s4c_new_ticker",
                                help="e.g. SJS.NS, ZENTEC.NS, RADICO.NS, ARROWGREEN.NS, DRAGARWQ.NS")
    with _c2:
        _add_tk = st.button("Add to Incubation", key="s4c_add_ticker")
    with _c3:
        _rem_tk = st.selectbox("Remove", ["(none)"] + sorted(inc_entries.keys()),
                               key="s4c_rem_ticker") if inc_entries else st.selectbox("Remove", ["(none)"],
                                                                                     key="s4c_rem_ticker_empty",
                                                                                     disabled=True)
    if _add_tk and _new_tk:
        _sid = str(_new_tk).strip().upper()
        try:
            _series = px.series(_sid, as_of) if hasattr(px, "series") else []
            if not _series:
                st.error(f"No price history found for {_sid} up to {as_of}.")
            else:
                _si = s4.StockInput(security_id=_sid, symbol=_sid, quantity=0.0,
                                    avg_buy_price=0.0, invested_cost=0.0, market_value=0.0,
                                    xirr=0.0, holding_start_date=as_of, price_series=_series,
                                    price_dates=_series)
                _tot = s4.PortfolioTotals(total_invested_cost=0.0, total_market_value=0.0)
                _m = s4.analyzeStockSignals(_si, _tot, 0.12)
                _starter = s4.incubation_starter_sip(_m)
                inc_entries[_sid] = {
                    "ticker": _sid,
                    "cmp": _m.get("cmp") or _series[-1],
                    "annual_volatility": _m.get("annual_volatility"),
                    "dma200_distance_pct": _m.get("dma200_distance_pct"),
                    "is_golden_cross": bool(_m.get("is_golden_cross")),
                    "starter_sip": float(_starter["sip"]),
                    "tier": _starter["tier"],
                    "reason": _starter["reason"],
                    "invested_cost": 0.0,
                }
                st.session_state["s4c_incubation_entries"] = inc_entries
                st.rerun()
        except Exception as _e:  # noqa: BLE001
            st.error(f"Could not simulate {_sid}: {_e}")
    if _rem_tk != "(none)" and _rem_tk in inc_entries:
        del inc_entries[_rem_tk]
        st.session_state["s4c_incubation_entries"] = inc_entries
        st.rerun()

    incubation_total = 0.0
    inc_rows = []
    if inc_entries:
        st.markdown("**Incubation Pacing Table** (< ₹1,00,000 — staged deployment)")
        for _tk in sorted(inc_entries):
            _e = inc_entries[_tk]
            _vol = _e["annual_volatility"]
            _dist = _e["dma200_distance_pct"]
            _sip = _e["starter_sip"]
            incubation_total += _sip
            _months = s4.months_to_graduate(_e["invested_cost"], _sip)
            _badge_txt = {
                "A": "🚀 Fast-Track ₹20k",
                "B": "⚖️ Standard ₹10k",
                "C": "🛡️ Toe-Hold ₹5k",
            }.get(_e["tier"], _e["tier"])
            inc_rows.append({
                "Ticker": _tk,
                "CMP": f"{_e['cmp']:,.1f}" if _e.get("cmp") else "N/A",
                "Vol %": f"{_vol * 100:.1f}%" if _vol is not None else "N/A",
                "200-DMA Dist %": f"{_dist * 100:+.1f}%" if _dist is not None else "N/A",
                "Starter SIP": f"₹{_sip:,.0f}",
                "Reason": f"{_badge_txt} — {_e['reason']}",
                "Months to Graduate": _months,
            })
        st.dataframe(pd.DataFrame(inc_rows), hide_index=True, width="stretch")
        st.caption("All incubation SIPs are exact ₹5,000 multiples. Tier A = low vol + golden cross + ideal pullback; "
                   "Tier B = standard cruising zone; Tier C = overheated (>+30% above 200-DMA) or trend not confirmed.")
        st.caption(f"Incubation monthly total: ₹{incubation_total:,.0f} — deducted from the monthly budget below.")

    # ---- monthly execution table ----------------------------------------
    def _badge(r):
        if r.get("signal_type") == s4.HARD_STOP_LOSS:
            return "0.0x EXIT 100% (Hard Stop-Loss Triggered)"
        if r.get("signal_type") == s4.LAGGARD_EXIT:
            return "0.0x LAGGARD EXIT (Held >1Yr & Trailing Benchmark)"
        if r.get("run_up_freeze") or r.get("over_allocated"):
            return "0.0x Run-Up / Over-Allocated - Pause SIP (Hold Winners)"
        inc = r.get("incubation")
        sip = float(r.get("sip_multiplier") or 0.0)
        if sip >= 2.5:
            return "2.5x Resting Winner"
        if sip >= 1.0:
            return "1.0x Incubating" if inc else "1.0x Cruising Stock"
        return "0.0x Broken Trend - Hold & Verify GTT"

    def _dma_dist(r):
        cmp_ = r.get("cmp")
        d200 = r.get("dma_200")
        if cmp_ and d200 and d200 > 0:
            pct = (cmp_ - d200) / d200 * 100.0
            return f"{pct:+.1f}% {'above' if pct >= 0 else 'below'}"
        return "N/A"

    st.markdown("**Monthly Execution Table**")
    disp_rows = []
    amber_flags = []
    csv_rows = []
    for r in arows:
        is_excluded = r.get("excluded", False)
        is_hard_stop = r.get("signal_type") == s4.HARD_STOP_LOSS
        am = (not is_hard_stop) and (
            bool(r.get("warning_level"))
            or (r.get("gtt_buffer_pct") is not None and r["gtt_buffer_pct"] <= s4.GTT_BUFFER_WARN)
        )
        amber_flags.append(am)
        stage = "Incubating" if r.get("incubation") else "Graduated"
        sip_amt = float(r.get("recommended_sip_rupees") or 0.0)
        if is_hard_stop:
            gtt_disp = "Exit Now (Do Not Set GTT)"
        elif r.get("gtt_stop_loss_price"):
            _anchor = r.get("gtt_anchor_type") or ""
            _ap = r.get("gtt_anchor_price")
            if _anchor and _ap:
                gtt_disp = f"{r['gtt_stop_loss_price']:,.1f} ({_anchor} from ₹{_ap:,.0f})"
            else:
                gtt_disp = f"{r['gtt_stop_loss_price']:,.1f}"
        else:
            gtt_disp = "N/A"
        disp_rows.append({
            "Stock": _name(r["security_id"]),
            "CMP": f"{r['cmp']:,.1f}" if r.get("cmp") else "N/A",
            "Stage": stage,
            "Excluded": str(is_excluded),
            "200-DMA": _dma_dist(r),
            "AER": f"{r['aer']:.2f}x" if r.get("aer") is not None else "N/A",
            "Signal": _badge(r),
            "SIP (Rs)": f"{sip_amt:,.0f}",
            "Shares": str(r.get("est_shares") or 0),
            "GTT (Rs)": gtt_disp,
            "Buffer": f"{r['gtt_buffer_pct'] * 100:.1f}%" if r.get("gtt_buffer_pct") is not None else "N/A",
            "Verify": "⚠ GTT" if am else "",
        })
        if is_hard_stop:
            _action = "EXIT_HARD_STOP"
        elif float(r.get("sip_multiplier") or 0) > 0:
            _action = "BUY"
        else:
            _action = "WARNING_HOLD_VERIFY_GTT"
        csv_rows.append({
            "Stock Ticker": r["security_id"],
            "Stage": stage,
            "Excluded": str(is_excluded),
            "Action": _action,
            "Lane / Signal": _badge(r),
            "SIP Amount (Rs)": round(sip_amt, 2),
            "Est. Shares": int(r.get("est_shares") or 0),
            "Updated GTT Trigger (Rs)": round(float(r.get("gtt_stop_loss_price") or 0.0), 2),
            "GTT Anchor Type": r.get("gtt_anchor_type") or "",
            "Note": "EXCLUDED — DO NOT BUY" if is_excluded else "",
        })

    df_disp = pd.DataFrame(disp_rows)

    def _hl(row):
        color = "background-color: #fff3cd; color: #7a5c00" if amber_flags[row.name] else ""
        return [color] * len(row)

    try:
        st.dataframe(df_disp.style.apply(_hl, axis=1), hide_index=True, width="stretch")
    except Exception:  # noqa: BLE001
        st.dataframe(df_disp, hide_index=True, width="stretch")
    st.caption("Rows highlighted amber are Warning Level or within 10% of the GTT trigger — verify these broker GTT orders before executing.")

    # ---- incubation rows in CSV export ------------------------------------
    for _tk in sorted(inc_entries):
        _e = inc_entries[_tk]
        csv_rows.append({
            "Stock Ticker": _tk,
            "Stage": "Incubating",
            "Action": "BUY",
            "Lane / Signal": f"{_e['tier']} - {_e['reason']}",
            "SIP Amount (Rs)": round(float(_e["starter_sip"]), 2),
            "Est. Shares": 0,
            "Updated GTT Trigger (Rs)": 0.0,
            "GTT Anchor Type": "Incubation 40% Catastrophe Floor",
        })

    csv_bytes = pd.DataFrame(csv_rows).to_csv(index=False).encode("utf-8")
    st.download_button("Download Monthly Order & GTT Sheet (CSV)", data=csv_bytes,
                       file_name="monthly_sip_gtt_plan.csv", mime="text/csv", key="s4c_csv")


def render(ctx: AppContext) -> None:
    f = ctx.filters
    st.markdown("### Direct Equity")
    st.caption("Direct equity portfolio performance vs benchmark, with sector, concentration and risk breakdowns.")
    if not f.show_equity:
        st.info("Select Direct Equity in the sidebar Asset type filter to view this page.")
    else:
        _render_equity_metrics(ctx)
        st.divider()
        _render_stock_attribution(ctx)
        st.divider()
        _render_whatif_simulator(ctx)
        st.divider()
        _render_stage4_action_center(ctx)
        st.divider()
        _render_stage4_planner(ctx)
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
