"""Page 1 — Portfolio.

Consolidated Equity + MF overview: total value, invested capital, gains, XIRR,
dividends, asset allocation, risk-adjusted metrics (volatility/Sharpe/Sortino/
max drawdown) and holding-level return attribution. Consolidates the former
Overview, MF Overview, Consolidated Overview and Consolidated Allocation tabs.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.components import ui
from app.context import AppContext
from app.shared import fmt_pct, inr, security_name


def _equity_value(holdings) -> float:
    total = 0.0
    if holdings is not None and not holdings.empty:
        for _, r in holdings.iterrows():
            cv = r.get("current_value")
            try:
                if cv is not None and pd.notna(cv):
                    total += float(cv)
            except (TypeError, ValueError):
                continue
    return total


def _holdings_frame(holdings) -> pd.DataFrame:
    if holdings is None or getattr(holdings, "empty", True):
        return pd.DataFrame()
    df = holdings.copy()
    if "isin" in df.columns:
        df["security_name"] = df["isin"].map(lambda x: security_name(x, holdings=holdings))
    return df


_MF_IN = {"SIP", "PURCHASE", "BUY", "LUMP_SUM", "SUBSCRIPTION", "ALLOTMENT", "BONUS",
          "GROWTH", "ACCUMULATION", "REINVEST"}
_MF_OUT = {"REDEMPTION", "SWP", "WITHDRAW", "SELL"}
_MF_DIV = {"IDCW", "DIVIDEND", "DIVIDEND_PAYOUT", "PAYOUT"}
_MF_BUY_UNITS = _MF_IN | {"SWITCH_IN", "STP_IN"}
_MF_SELL_UNITS = _MF_OUT | {"SWITCH_OUT", "STP_OUT"}


def _mf_folder(ctx: AppContext) -> str:
    import re
    from pathlib import Path

    from app.shared import resolve_person_name

    person = resolve_person_name(
        ctx.xirr_db_path,
        getattr(ctx.filters, "person_id", None),
        getattr(ctx.filters, "person_name", None),
    )
    if not person:
        return "data/mf"
    slug = re.sub(r"[^a-z0-9]+", "_", str(person).lower()).strip("_") or "person"
    return str(Path("data/mf") / slug)


def _mf_scope(ctx: AppContext):
    """Return (mf_context, nav_by_scheme, mf_transactions, mf_folder)."""
    from app.mf_ui import get_mf_context

    mf = get_mf_context(ctx)
    nav_by_scheme = {}
    for r in mf.get("scheme_rows", []):
        code = str(r.get("scheme_code") or "")
        nav = r.get("nav")
        if code and nav:
            nav_by_scheme[code] = float(nav)
    folder = _mf_folder(ctx)
    try:
        from mf.service import _load_transactions_from_folder
        mf_txs = _load_transactions_from_folder(folder)
    except Exception:  # noqa: BLE001
        mf_txs = []
    return mf, nav_by_scheme, mf_txs, folder


def _mf_value_at(mf_txs, nav_by_scheme, as_of) -> float:
    end = str(as_of)[:10]
    units: dict[str, float] = {}
    for t in mf_txs or []:
        d = str(t.get("trade_date") or "")[:10]
        if not d or d > end:
            continue
        code = str(t.get("scheme_code") or "")
        if not code:
            continue
        side = str(t.get("side") or "").upper()
        u = float(t.get("units") or 0)
        if side in _MF_BUY_UNITS:
            units[code] = units.get(code, 0.0) + u
        elif side in _MF_SELL_UNITS:
            units[code] = max(0.0, units.get(code, 0.0) - u)
    val = 0.0
    for code, u in units.items():
        nav = nav_by_scheme.get(code)
        if nav and u > 0:
            val += u * nav
    return val


def _mf_netflow(mf_txs, ys: str, ye: str) -> float:
    tot = 0.0
    for t in mf_txs or []:
        d = str(t.get("trade_date") or "")[:10]
        if not d or not (ys <= d <= ye):
            continue
        side = str(t.get("side") or "").upper()
        amt = abs(float(t.get("amount") or 0))
        if side in _MF_IN:
            tot += amt
        elif side in _MF_OUT:
            tot -= amt
    return tot


def _mf_cashflows(mf_txs, end_date, terminal: float) -> list[tuple[str, float]]:
    flows: list[tuple[str, float]] = []
    for t in mf_txs or []:
        d = str(t.get("trade_date") or "")[:10]
        if not d:
            continue
        side = str(t.get("side") or "").upper()
        amt = abs(float(t.get("amount") or 0))
        if amt <= 0:
            continue
        if side in _MF_IN:
            flows.append((d, -amt))
        elif side in _MF_OUT or side in _MF_DIV:
            flows.append((d, amt))
    if terminal and terminal > 0:
        flows.append((str(end_date)[:10], float(terminal)))
    return flows


def _blended_xirr(equity_flows, mf_flows):
    from src.xirr import compute_xirr

    flows: list[tuple] = []
    for f in equity_flows or []:
        try:
            flows.append((f[0], float(f[1])))
        except (TypeError, ValueError, IndexError):
            continue
    flows.extend(mf_flows or [])
    if len(flows) < 2:
        return None
    try:
        return compute_xirr(flows)
    except Exception:  # noqa: BLE001
        return None


def _mf_cost_basis_upto(mf_txs, end) -> float:
    """FIFO cost basis of MF units still held as of ``end`` (transactions up to that date)."""
    end = str(end)[:10] if end is not None else "9999-12-31"
    upto = [t for t in (mf_txs or [])
            if str(t.get("trade_date") or "")[:10] and str(t.get("trade_date"))[:10] <= end]
    try:
        from mf.accounting import _fifo_gains_for_scheme
    except Exception:  # noqa: BLE001
        return 0.0
    total = 0.0
    for code in {str(t.get("scheme_code") or "") for t in upto}:
        if not code:
            continue
        try:
            total += float(_fifo_gains_for_scheme(upto, code)["remaining_cost"])
        except Exception:  # noqa: BLE001, S110
            pass
    return total


def _classify_mf_scheme(category, scheme_name):
    txt=(str(category or "")+" "+str(scheme_name or "")).lower()
    if any(k in txt for k in ("gold","silver","commodity","international","foreign","overseas")): return {"Alternatives":1.0}
    if any(k in txt for k in ("liquid","overnight","gilt","money market","bond","credit risk","corporate bond","debt","stip","sdl")): return {"Debt":1.0}
    if any(k in txt for k in ("hybrid","balanced","multi asset","arbitrage","equity savings","aggressive","conservative")): return {"Equity":0.65,"Debt":0.35}
    return {"Equity":1.0}


def _annualize_ret(total, days):
    if total is None or not days or days <= 0:
        return None
    base = 1.0 + float(total)
    if base <= 0:
        return None
    try:
        return base ** (365.0 / days) - 1.0
    except Exception:  # noqa: BLE001
        return None

def _bundle_returns(ctx, mf, mf_txs, nav_by_scheme, start, end):
    from datetime import date as _d

    from market_data.prices import get_index_tri
    from performance.xirr import calculate_portfolio_xirr
    res = {"equity": None, "mf": None, "consolidated": None, "nifty50": None, "nifty500": None}
    sd = start if isinstance(start, _d) else _d.fromisoformat(str(start)[:10])
    ed = end if isinstance(end, _d) else _d.fromisoformat(str(end)[:10])
    days = (ed - sd).days
    if days <= 0:
        return res
    try:
        eres_e = calculate_portfolio_xirr(ctx.txs, actions=ctx.corporate_actions, dividends=ctx.dividend_flows,
                                          start_date=sd, end_date=ed, price_lookup=ctx.pl,
                                          opening_positions=ctx.opening_positions)
        res["equity"] = eres_e.get("xirr")
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        flows = []
        mv0 = _mf_value_at(mf_txs, nav_by_scheme, sd)
        if mv0 > 0:
            flows.append((str(sd)[:10], -mv0))
        for t in mf_txs or []:
            d = str(t.get("trade_date") or "")[:10]
            if not d or not (str(sd)[:10] <= d <= str(ed)[:10]):
                continue
            side = str(t.get("side") or "").upper()
            amt = abs(float(t.get("amount") or 0))
            if side in _MF_IN:
                flows.append((d, -amt))
            elif side in _MF_OUT or side in _MF_DIV:
                flows.append((d, amt))
        mv = _mf_value_at(mf_txs, nav_by_scheme, ed)
        if mv > 0:
            flows.append((str(ed)[:10], mv))
        if flows:
            res["mf"] = _blended_xirr([], flows)
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        eres = calculate_portfolio_xirr(ctx.txs, actions=ctx.corporate_actions, dividends=ctx.dividend_flows,
                                        start_date=sd, end_date=ed, price_lookup=ctx.pl,
                                        opening_positions=ctx.opening_positions)
        eflows = eres.get("cashflows") or []
        mfflows = []
        mv0 = _mf_value_at(mf_txs, nav_by_scheme, sd)
        if mv0 > 0:
            mfflows.append((str(sd)[:10], -mv0))
        for t in mf_txs or []:
            d = str(t.get("trade_date") or "")[:10]
            if not d or not (str(sd)[:10] <= d <= str(ed)[:10]):
                continue
            side = str(t.get("side") or "").upper()
            amt = abs(float(t.get("amount") or 0))
            if side in _MF_IN:
                mfflows.append((d, -amt))
            elif side in _MF_OUT or side in _MF_DIV:
                mfflows.append((d, amt))
        mv = _mf_value_at(mf_txs, nav_by_scheme, ed)
        if mv > 0:
            mfflows.append((str(ed)[:10], mv))
        res["consolidated"] = _blended_xirr(eflows, mfflows)
    except Exception:  # noqa: BLE001, S110
        pass
    for iid, key in (("NIFTY50TRI", "nifty50"), ("NIFTY500TRI", "nifty500")):
        try:
            a = get_index_tri(iid, str(sd)[:10], db_path=ctx.market_db_path)
            b = get_index_tri(iid, str(ed)[:10], db_path=ctx.market_db_path)
            if a and b:
                res[key] = _annualize_ret(float(b) / float(a) - 1.0, days)
        except Exception:  # noqa: BLE001, S110
            pass
    return res

def _donut(buckets):
    import plotly.graph_objects as go
    data = {k: v for k, v in buckets.items() if v and v > 0}
    total = sum(data.values())
    if total <= 0:
        st.caption("No allocation data.")
        return
    c1, c2 = st.columns([1, 1])
    with c1:
        for k, v in data.items():
            st.markdown(f"**{k} {v / total * 100:.1f}%**")
    with c2:
        colors = ["#33689e", "#6ba26b", "#a5653f", "#9b6f9e", "#cccccc"]
        fig = go.Figure(go.Pie(labels=list(data.keys()), values=list(data.values()), hole=0.55,
                               marker={"colors": colors[: len(data)]},
                               textinfo="percent", textposition="inside"))
        fig.update_layout(height=300, margin={"t": 10, "b": 10, "l": 10, "r": 10}, showlegend=False)
        st.plotly_chart(fig, width="stretch")

def _render_allocation_section(ctx, mf, holdings):
    st.markdown("#### Allocation")
    eqv = _equity_value(holdings)
    alloc = _allocation_buckets(eqv, mf)
    t1, t2 = st.tabs(["BY ASSET", "BY INVESTMENT TYPE"])
    with t1:
        _donut({"Equity": alloc["Equity"], "Debt": alloc["Debt"],
                "Alternatives": alloc["Alternatives"], "Others": alloc["Others"]})
        st.caption("Debt includes MF debt/liquid schemes; hybrid debt sleeve assumed 35%.")
    with t2:
        _donut({"Direct Equity": alloc["Direct Equity"], "Mutual Funds": alloc["Mutual Funds"]})

def _render_returns_by_investment_type(ctx, mf, holdings, mf_txs, nav_by_scheme):
    from datetime import date as _d

    import pandas as pd
    st.markdown("#### Returns by Investment Type")
    f = ctx.filters
    today = f.end_date or _d.today()
    today = today if isinstance(today, _d) else _d.fromisoformat(str(today)[:10])

    def _pct(v):
        return "N/A" if v is None else f"{v * 100:.2f}% p.a."

    dur = st.selectbox("DURATION", ["1 Year", "2 Years", "3 Years", "4 Years", "5 Years", "Since inception"], key="returns_dur")
    if dur == "1 Year":
        start = _d(today.year - 1, today.month, min(today.day, 28))
    elif dur == "2 Years":
        start = _d(today.year - 2, today.month, min(today.day, 28))
    elif dur == "3 Years":
        start = _d(today.year - 3, today.month, min(today.day, 28))
    elif dur == "4 Years":
        start = _d(today.year - 4, today.month, min(today.day, 28))
    elif dur == "5 Years":
        start = _d(today.year - 5, today.month, min(today.day, 28))
    else:
        start = f.start_date or _d(2000, 1, 1)
    start = start if isinstance(start, _d) else _d.fromisoformat(str(start)[:10])
    b = _bundle_returns(ctx, mf, mf_txs, nav_by_scheme, start, today)
    st.caption(f"Period: {start} to {today}")
    rows = [
        {"Investment Type": "Mutual Funds / SIFs", "Return": _pct(b["mf"]), "Nifty50 TRI": _pct(b["nifty50"]), "Nifty500 TRI": _pct(b["nifty500"])},
        {"Investment Type": "Stocks (Direct Equity)", "Return": _pct(b["equity"]), "Nifty50 TRI": _pct(b["nifty50"]), "Nifty500 TRI": _pct(b["nifty500"])},
        {"Investment Type": "Consolidated", "Return": _pct(b["consolidated"]), "Nifty50 TRI": _pct(b["nifty50"]), "Nifty500 TRI": _pct(b["nifty500"])},
    ]
    ui.safe_dataframe(pd.DataFrame(rows))

    st.markdown("##### Returns by year")
    _yrs = [x for x in _year_options(ctx.txs, mf_txs, f) if x != "Since inception"]
    if not _yrs:
        st.caption("No year data available.")
        return
    ysel = st.selectbox("Year", _yrs, key="returns_year")
    yy = int(ysel)
    ys = _d(yy, 1, 1)
    if f.start_date and ys < f.start_date:
        ys = f.start_date if isinstance(f.start_date, _d) else _d.fromisoformat(str(f.start_date)[:10])
    ye = min(_d(yy, 12, 31), today)
    bb = _bundle_returns(ctx, mf, mf_txs, nav_by_scheme, ys, ye)
    yrow = [{"Year": str(yy), "Consolidated": _pct(bb["consolidated"]), "Direct Equity": _pct(bb["equity"]),
             "Mutual Funds": _pct(bb["mf"]), "Nifty50 TRI": _pct(bb["nifty50"]), "Nifty500 TRI": _pct(bb["nifty500"])}]
    ui.safe_dataframe(pd.DataFrame(yrow))


def _period_bounds(f, sel):
    from datetime import date as _d
    if sel == "Since inception":
        return f.start_date, f.end_date
    yy = int(sel)
    ps = _d(yy, 1, 1)
    if f.start_date and ps < f.start_date:
        ps = f.start_date
    pe = min(_d(yy, 12, 31), _d.today())
    if f.end_date and pe > f.end_date:
        pe = f.end_date
    return ps, pe

def _year_options(txs, mf_txs, f):
    from datetime import date as _d
    years = set()
    for x in (txs or []):
        s = str(x.get("trade_date") or x.get("settlement_date") or "")[:4]
        if s.isdigit():
            years.add(int(s))
    for t in (mf_txs or []):
        s = str(t.get("trade_date") or "")[:4]
        if s.isdigit():
            years.add(int(s))
    so = f.start_date.year if f.start_date else None
    eo = f.end_date.year if f.end_date else None
    yopts = sorted([y for y in years if (so is None or y >= so) and (eo is None or y <= eo)], reverse=True)
    if not yopts:
        yopts = sorted(years, reverse=True) or [eo or _d.today().year]
    return ["Since inception"] + [str(y) for y in yopts]

def _allocation_buckets(eq_value, mf):
    ae = float(eq_value or 0); ad = 0.0; aa = 0.0; ao = 0.0
    mt = float(mf.get("total_value") or 0)
    for r in mf.get("scheme_rows", []):
        v = float(r.get("current_value") or 0)
        if v <= 0:
            continue
        parts = _classify_mf_scheme(r.get("category"), r.get("scheme_name"))
        for k, rr in parts.items():
            if k == "Equity": ae += v * rr
            elif k == "Debt": ad += v * rr
            elif k == "Alternatives": aa += v * rr
            elif k == "Others": ao += v * rr
    ta = ae + ad + aa + ao; ex = mt + float(eq_value or 0)
    if abs(ta - ex) > 1.0:
        ao += ex - ta
    return {"Equity": round(ae, 2), "Debt": round(ad, 2), "Alternatives": round(aa, 2), "Others": round(ao, 2), "Direct Equity": round(float(eq_value or 0), 2), "Mutual Funds": round(mt, 2)}

def _cumulative_series(rows):
    xs, ypf, y50, y500 = [], [], [], []
    first_value = None
    n50 = n500 = 1.0
    has50 = has500 = False
    for r in rows or []:
        xs.append(str(r.get("period")))
        value = r.get("portfolio_value")
        a = r.get("nifty50_tri_return")
        b = r.get("nifty500_tri_return")
        if first_value is None:
            if isinstance(value, (int, float)) and float(value) > 0:
                first_value = float(value)
                ypf.append(0.0)
            else:
                ypf.append(None)
        else:
            if isinstance(value, (int, float)) and first_value > 0:
                ypf.append(round((float(value) / first_value - 1.0) * 100.0, 4))
            else:
                ypf.append(None)
        if a is not None:
            has50 = True
            n50 *= (1.0 + float(a))
            y50.append(round((n50 - 1.0) * 100.0, 4))
        else:
            y50.append(None)
        if b is not None:
            has500 = True
            n500 *= (1.0 + float(b))
            y500.append(round((n500 - 1.0) * 100.0, 4))
        else:
            y500.append(None)
    return xs, ypf, y50, y500, has50, has500


def _render_risk_metrics(ctx: AppContext) -> None:
    """Risk-adjusted metrics card (volatility, Sharpe, Sortino, max drawdown).

    Respects Page 1's active Asset Type / Member / date filters (already applied
    to ``ctx.txs`` and ``ctx.pl``). Rendered directly below the Allocation panel.
    """
    from datetime import date as _d

    from mf import analytics as mf_analytics
    from performance.xirr import portfolio_value_series

    st.markdown("#### Risk-Adjusted Metrics")
    f = ctx.filters
    start = f.start_date
    end = f.end_date or _d.today()

    try:
        series = portfolio_value_series(
            ctx.txs,
            price_lookup=ctx.pl,
            start_date=start,
            end_date=end,
            opening_positions=ctx.opening_positions,
        )
    except Exception:  # noqa: BLE001
        series = []

    if len(series) < 3:
        st.caption("Not enough monthly valuation points in this period to compute risk metrics.")
        return

    rets = mf_analytics.periodic_returns(series)
    vol = mf_analytics.annualized_volatility(rets, periods_per_year=12)
    sharpe = mf_analytics.sharpe_ratio(rets, risk_free_annual=0.06, periods_per_year=12)
    sortino = mf_analytics.sortino_ratio(rets, risk_free_annual=0.06, periods_per_year=12)
    mdd = mf_analytics.max_drawdown(series)

    def _p(v):
        return fmt_pct(v) if v is not None else "N/A"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Annualised Volatility", _p(vol))
    c2.metric("Sharpe (rf 6%)", f"{sharpe:.2f}" if sharpe is not None else "N/A")
    c3.metric("Sortino (rf 6%)", f"{sortino:.2f}" if sortino is not None else "N/A")
    c4.metric("Max Drawdown", _p(mdd.get("max_drawdown")) if mdd else "N/A")
    if mdd:
        st.caption(
            f"Max drawdown peak {mdd.get('peak_date')} \u2192 trough {mdd.get('trough_date')}. "
            f"Monthly valuations: {len(series)} points."
        )
    st.caption(
        "Volatility / Sharpe / Sortino use month-end portfolio valuations "
        "(12 periods per year) with a 6% annual risk-free rate."
    )


def _render_holding_attribution(ctx: AppContext, holdings) -> None:
    """Stock-level return attribution table (current holdings + sold-off positions).

    Filtered by Page 1's active Asset Type and Member filters (member scoping is
    already applied to ``ctx.txs``; asset type gates whether equity rows show).
    """
    from collections import defaultdict
    from datetime import date as _d

    import pandas as pd

    st.markdown("#### Holding-Level Return Attribution")
    f = ctx.filters
    if f.asset_type == "Mutual Funds":
        st.caption("Switch Asset Type to 'All' or 'Direct Equity' to see stock-level attribution.")
        return

    from accounting.fifo import get_fifo_lots
    from performance import equity_analytics as ea

    txs = ctx.txs
    end = f.end_date or _d.today()
    end = end if isinstance(end, _d) else _d.fromisoformat(str(end)[:10])
    start = f.start_date
    if start is None:
        start = _d(2000, 1, 1)
    elif not isinstance(start, _d):
        start = _d.fromisoformat(str(start)[:10])

    def _idx(d):
        try:
            from market_data.prices import get_index_tri

            return get_index_tri("NIFTY50TRI", str(d)[:10], db_path=ctx.market_db_path)
        except Exception:  # noqa: BLE001
            return None

    lots = get_fifo_lots(txs, None, as_of_date=end)
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
            p = ctx.pl(sid, end)
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
        sx = ea.stock_xirr(txs, ctx.dividend_flows, sid, start, end,
                           terminal_value=cv, terminal_date=end)
        stwr = ea.stock_twr(txs, ctx.pl, sid, start, end)
        flows = ea.stock_flows(txs, ctx.dividend_flows, sid, start, end)
        bx = ea.benchmark_xirr_for_flows(flows, _idx, start, end)
        al = ea.alpha(sx, bx)
        weight = (cv / total_mv * 100.0) if (cv and total_mv > 0) else None
        current_rows.append({
            "Stock": security_name(sid, txs, holdings),
            "Sector": sector_map.get(sid) or "",
            "Invested": round(cost, 2),
            "Current": round(cv, 2) if cv is not None else None,
            "Gain": (round(cv - cost, 2) if cv is not None else None),
            "Weight %": (None if weight is None else round(weight, 2)),
            "Stock XIRR %": (None if sx is None else round(sx * 100.0, 2)),
            "Stock TWR %": (None if stwr is None else round(stwr * 100.0, 2)),
            "Alpha %": (None if al is None else round(al * 100.0, 2)),
        })

    st.markdown("##### Current Holdings")
    ui.safe_dataframe(pd.DataFrame(current_rows), key="p1_attr_current",
                      empty_message="No current equity holdings.")

    sold_isins = set()
    for t in txs or []:
        if str(t.get("buy_sell") or "").strip().upper() == "SELL":
            sid = str(t.get("isin") or "").strip().upper()
            if sid:
                sold_isins.add(sid)
    exited_isins = [sid for sid in sold_isins if agg.get(sid, [0.0, 0.0])[0] <= 1e-6]

    exited_rows = []
    for sid in sorted(exited_isins):
        buys, sells = [], []
        for t in txs or []:
            if str(t.get("isin") or "").strip().upper() != sid:
                continue
            try:
                dd = ea._to_date(ea._tx_date(t))
            except (TypeError, ValueError):
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
        deployed = sum(a for _, a in buys)
        exit_val = sum(a for _, a in sells)
        pnl = exit_val - deployed
        pnl_pct = (pnl / deployed * 100.0) if deployed else None
        rx = ea.stock_xirr(txs, ctx.dividend_flows, sid, first_buy, final_sell)
        exited_rows.append({
            "Stock": security_name(sid, txs, holdings),
            "Sector": sector_map.get(sid) or "",
            "Holding Period": f"{first_buy} to {final_sell}",
            "Deployed": round(deployed, 2),
            "Exit Value": round(exit_val, 2),
            "P&L": round(pnl, 2),
            "P&L %": (None if pnl_pct is None else round(pnl_pct, 2)),
            "Realized XIRR %": (None if rx is None else round(rx * 100.0, 2)),
        })

    if exited_rows:
        st.markdown("##### Sold-Off Positions")
        ui.safe_dataframe(pd.DataFrame(exited_rows), key="p1_attr_exited")


def render(ctx: AppContext) -> None:
    holdings = ctx.holdings

    st.markdown("### Portfolio Overview")
    st.caption("Consolidated Equity + Mutual Fund view with allocation, risk & attribution insights.")

    _mf, _nav_by_scheme, _mf_txs, _mf_folder = _mf_scope(ctx)
    _render_consolidated(ctx, _mf, _nav_by_scheme, _mf_txs)
    st.divider()

    _render_allocation_section(ctx, _mf, holdings)
    st.divider()

    _render_risk_metrics(ctx)
    st.divider()

    _render_holding_attribution(ctx, holdings)
    st.divider()

    _render_returns_by_investment_type(ctx, _mf, holdings, _mf_txs, _nav_by_scheme)


def _render_consolidated(ctx, mf, nav_by_scheme, mf_txs) -> None:
    from market_data.prices import get_index_tri
    from performance.xirr import calculate_portfolio_snapshot, calculate_portfolio_xirr

    f = ctx.filters
    txs = ctx.txs
    st.markdown("#### Consolidated Portfolio (Equity + Mutual Funds)")
    opts = _year_options(txs, mf_txs, f)
    if st.session_state.get("cons_period") not in opts:
        st.session_state["cons_period"] = "Since inception"
    sel = st.selectbox("Period", opts, key="cons_period")
    p_start, p_end = _period_bounds(f, sel)
    valid = p_start is not None and p_end is not None and p_start <= p_end

    eq_val = eq_inv = 0.0
    if txs and valid:
        try:
            snap = calculate_portfolio_snapshot(txs, price_lookup=ctx.pl, as_of_date=p_end,
                                                 opening_positions=ctx.opening_positions)
            eq_val = float(snap.get("portfolio_value") or 0)
            eq_inv = float(snap.get("invested_capital") or 0)
        except Exception:  # noqa: BLE001, S110
            pass
    # ---- Period-aware MF value & invested (cost basis of held units) at p_end ----
    mf_flow_map: dict[str, float] = {}
    for t in mf_txs or []:
        d = str(t.get("trade_date") or "")[:10]
        if not d or d > str(p_end)[:10]:
            continue
        side = str(t.get("side") or "").upper()
        amt = abs(float(t.get("amount") or 0))
        if side in _MF_IN:
            mf_flow_map[d] = mf_flow_map.get(d, 0.0) - amt
        elif side in _MF_OUT or side in _MF_DIV:
            mf_flow_map[d] = mf_flow_map.get(d, 0.0) + amt
    mf_inv_period = _mf_cost_basis_upto(mf_txs, p_end) if valid else float(mf.get("total_invested") or 0)
    mf_val = _mf_value_at(mf_txs, nav_by_scheme, p_end) if valid else float(mf.get("total_value") or 0)
    if valid:
        mf_flow_map[str(p_end)[:10]] = mf_flow_map.get(str(p_end)[:10], 0.0) + mf_val
    mf_flows = [(d, v) for d, v in sorted(mf_flow_map.items())]

    # ---- Equity cash flows for the period ----
    eflows = []
    if txs and valid:
        try:
            eres = calculate_portfolio_xirr(txs, actions=ctx.corporate_actions, dividends=ctx.dividend_flows,
                                            start_date=p_start, end_date=p_end, price_lookup=ctx.pl,
                                            opening_positions=ctx.opening_positions)
            eflows = eres.get("cashflows") or []
        except Exception:  # noqa: BLE001
            eflows = []

    total_val = eq_val + mf_val
    total_inv = eq_inv + mf_inv_period
    cons_xirr = _blended_xirr(eflows, mf_flows)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Portfolio Value", inr(total_val))
    c2.metric("Invested Capital", inr(total_inv))
    c3.metric("Total Gains", inr(total_val - total_inv))
    c4.metric("Consolidated XIRR", fmt_pct(cons_xirr) if cons_xirr is not None else "N/A")
    if valid:
        st.caption(f"Consolidated values as of {str(p_end)[:10]} \u00b7 blended cash-flow-matched XIRR across "
                   "equity + MF cash flows. MF value uses period-end NAV.")

    if valid and (txs or mf_txs):
        from datetime import date as _d

        import plotly.graph_objects as go
        s = p_start if isinstance(p_start, _d) else _d.fromisoformat(str(p_start)[:10])
        e = p_end if isinstance(p_end, _d) else _d.fromisoformat(str(p_end)[:10])
        rows = []
        for y in range(s.year, e.year + 1):
            ys, ye = max(_d(y, 1, 1), s), min(_d(y, 12, 31), e)
            ev = 0.0
            if txs:
                try:
                    es = calculate_portfolio_snapshot(txs, price_lookup=ctx.pl, as_of_date=ye,
                                                      opening_positions=ctx.opening_positions)
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
            mv = _mf_value_at(mf_txs, nav_by_scheme, ye)
            mfl = _mf_netflow(mf_txs, str(ys)[:10], str(ye)[:10])
            r50 = r500 = None
            a50 = get_index_tri("NIFTY50TRI", str(ys)[:10], db_path=ctx.market_db_path)
            b50 = get_index_tri("NIFTY50TRI", str(ye)[:10], db_path=ctx.market_db_path)
            if a50 and b50:
                r50 = float(b50) / float(a50) - 1.0
            a500 = get_index_tri("NIFTY500TRI", str(ys)[:10], db_path=ctx.market_db_path)
            b500 = get_index_tri("NIFTY500TRI", str(ye)[:10], db_path=ctx.market_db_path)
            if a500 and b500:
                r500 = float(b500) / float(a500) - 1.0
            rows.append({"period": str(y), "portfolio_value": ev + mv, "net_flow": eqflow + mfl,
                         "nifty50_tri_return": r50, "nifty500_tri_return": r500})
        xs = [str(r.get("period")) for r in rows]
        pf_vals = []
        for r in rows:
            v = r.get("portfolio_value")
            pf_vals.append(float(v) if isinstance(v, (int, float)) else None)

        def _bench_series(key):
            out = []
            bench = None
            for rr in rows:
                v = rr.get("portfolio_value")
                try:
                    f = float(rr.get("net_flow") or 0.0)
                except (TypeError, ValueError):
                    f = 0.0
                rt = rr.get(key)
                if bench is None:
                    if isinstance(v, (int, float)) and float(v) > 0:
                        bench = float(v)
                    out.append(None if bench is None else round(bench, 2))
                    continue
                bench = (bench + f) * (1.0 + float(rt)) if rt is not None else bench + f
                out.append(round(bench, 2))
            return out

        b50 = _bench_series("nifty50_tri_return")
        b500 = _bench_series("nifty500_tri_return")
        has50 = any(v is not None for v in b50)
        has500 = any(v is not None for v in b500)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=xs, y=pf_vals, mode="lines+markers", name="My portfolio"))
        if has50:
            fig.add_trace(go.Scatter(x=xs, y=b50, mode="lines+markers", name="Nifty 50 TRI (same cashflows)"))
        if has500:
            fig.add_trace(go.Scatter(x=xs, y=b500, mode="lines+markers", name="Nifty 500 TRI (same cashflows)"))
        fig.update_layout(title="Portfolio value vs benchmarks (same cashflows invested)",
                          yaxis={"title": "Value (INR)"},
                          height=400, legend={"orientation": "h"},
                          margin={"t": 50, "b": 40, "l": 55, "r": 20},
                          plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")
        st.caption("Benchmark lines invest the same net contributions as your portfolio on the same dates, "
                   "growing at Nifty TRI returns, so values are directly comparable.")


def _render_benchmark_growth(ctx, start, end) -> None:
    from datetime import date as _d

    import plotly.graph_objects as go

    from app.shared import tx_hash
    from market_data.prices import get_index_tri
    from performance.xirr import calculate_portfolio_snapshot, calculate_portfolio_xirr

    md = ctx.market_db_path
    key = ("bench_growth_v2", str(start)[:10], str(end)[:10], ctx.filters.person_id, tx_hash(ctx.txs))
    rows = st.session_state.get(key)
    if rows is None:
        try:
            s = start if isinstance(start, _d) else _d.fromisoformat(str(start)[:10])
            e = end if isinstance(end, _d) else _d.fromisoformat(str(end)[:10])
            rows = []
            with st.spinner("Computing yearly portfolio vs benchmark returns..."):
                for y in range(s.year, e.year + 1):
                    ys = max(_d(y, 1, 1), s)
                    ye = min(_d(y, 12, 31), e)
                    res_y = calculate_portfolio_xirr(
                        ctx.txs,
                        actions=ctx.corporate_actions,
                        dividends=ctx.dividend_flows,
                        start_date=ys,
                        end_date=ye,
                        price_lookup=ctx.pl,
                        opening_positions=ctx.opening_positions,
                    )
                    r50 = None
                    r500 = None
                    try:
                        a50 = get_index_tri("NIFTY50TRI", ys.isoformat(), db_path=md)
                        b50 = get_index_tri("NIFTY50TRI", ye.isoformat(), db_path=md)
                        if a50 and b50:
                            r50 = float(b50) / float(a50) - 1.0
                    except Exception:  # noqa: BLE001
                        r50 = None
                    try:
                        a500 = get_index_tri("NIFTY500TRI", ys.isoformat(), db_path=md)
                        b500 = get_index_tri("NIFTY500TRI", ye.isoformat(), db_path=md)
                        if a500 and b500:
                            r500 = float(b500) / float(a500) - 1.0
                    except Exception:  # noqa: BLE001
                        r500 = None
                    try:
                        _snap = calculate_portfolio_snapshot(
                            ctx.txs, price_lookup=ctx.pl, as_of_date=ye,
                            opening_positions=ctx.opening_positions,
                        )
                        _pv = _snap.get("portfolio_value")
                    except Exception:  # noqa: BLE001
                        _pv = None
                    _netflow = 0.0
                    for _t in ctx.txs:
                        _td = str(_t.get("trade_date") or _t.get("settlement_date") or "")[:10]
                        if not _td or not (str(ys)[:10] <= _td <= str(ye)[:10]):
                            continue
                        _amt = float(_t.get("net_amount") or 0.0)
                        _side = str(_t.get("buy_sell") or "").upper()
                        if _side == "BUY":
                            _netflow += _amt
                        elif _side == "SELL":
                            _netflow -= _amt
                    rows.append({
                        "period": str(y),
                        "portfolio_xirr": res_y.get("xirr") if isinstance(res_y, dict) else None,
                        "portfolio_value": _pv,
                        "net_flow": _netflow,
                        "nifty50_tri_return": r50,
                        "nifty500_tri_return": r500,
                    })
            st.session_state[key] = rows
        except Exception as ex:  # noqa: BLE001
            st.caption("Annual comparison unavailable: " + str(ex))
            return
    if not rows:
        return

    xs, ypf, y50, y500, has50, has500 = _cumulative_series(rows)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=ypf, mode="lines+markers", name="Portfolio"))
    if has50:
        fig.add_trace(go.Scatter(x=xs, y=y50, mode="lines+markers", name="Nifty 50 TRI"))
    if has500:
        fig.add_trace(go.Scatter(x=xs, y=y500, mode="lines+markers", name="Nifty 500 TRI"))
    fig.update_layout(
        title="Cumulative growth (compounded calendar-year returns)",
        yaxis_title="Cumulative return (%)",
        height=420,
        legend={"orientation": "h"},
        margin={"t": 50, "b": 50, "l": 55, "r": 20},
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Portfolio is a contribution-neutral growth index (year-end market value adjusted "
        "for net deposits/withdrawals); benchmarks compound Nifty TRI yearly returns. "
        "Years with no usable value/price are carried flat."
    )


_XIRR_FLOW_LABELS = {
    "buy": "Buy (outflow)",
    "sell": "Sell (inflow)",
    "dividend": "Dividend (inflow)",
    "opening_value": "Opening value (outflow)",
    "terminal_value": "Terminal value (inflow)",
    "rights_subscription": "Rights subscription (outflow)",
}


def render_xirr_diagnostics(ctx, res, start, end, key_prefix: str = "xirr") -> None:
    """Audit and explain the Portfolio XIRR shown above.

    Three parts:
      1. the exact cash flows feeding the XIRR (auditability),
      2. a timing-vs-selection decomposition (why XIRR can beat a benchmark),
      3. risk-adjusted metrics (volatility / Sharpe / Sortino / max drawdown)
         that plain XIRR hides.
    """
    import pandas as pd

    from mf import analytics as mf_analytics
    from performance.benchmarks import calculate_benchmark_xirr
    from performance.xirr import calculate_portfolio_twr, portfolio_value_series

    st.markdown("#### XIRR Diagnostics")
    st.caption(
        "Where the Portfolio XIRR comes from, how much of it is cash-flow timing "
        "versus stock selection, and the risk behind the return."
    )

    flows = list(res.get("cashflows") or [])
    port_xirr = res.get("xirr")

    def _pct(v):
        return fmt_pct(v) if v is not None else "N/A"

    from datetime import date as _date

    def _as_date(v):
        if isinstance(v, _date):
            return v
        try:
            return _date.fromisoformat(str(v)[:10])
        except (TypeError, ValueError):
            return None

    def _annualize(total, days):
        if total is None or not days or days <= 0:
            return None
        try:
            base = 1.0 + float(total)
            if base <= 0:
                return None  # a wiped-out base would yield a complex power
            return base ** (365.0 / days) - 1.0
        except (ValueError, OverflowError, TypeError):
            return None

    def _num(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    sd = _as_date(start)
    ed = _as_date(end)
    days = (ed - sd).days if (sd is not None and ed is not None and ed > sd) else None

    # ---- 1. Cash-flow audit ------------------------------------------------
    with st.expander("Cash-flow audit — every flow used in the XIRR", expanded=False):
        if not flows:
            st.caption("No cash flows available for this period.")
        else:
            rows = []
            cum = 0.0
            for d, amt, lbl in sorted(flows, key=lambda x: (str(x[0])[:10], str(x[2]))):
                cum += float(amt)
                rows.append({
                    "Date": str(d)[:10],
                    "Flow": _XIRR_FLOW_LABELS.get(lbl, str(lbl)),
                    "Amount (Rs)": round(float(amt), 2),
                    "Cumulative (Rs)": round(cum, 2),
                })
            audit_df = pd.DataFrame(rows)
            ui.safe_dataframe(audit_df)
            st.download_button(
                "Download cash-flow CSV",
                data=audit_df.to_csv(index=False).encode("utf-8"),
                file_name=f"xirr_cashflows_{key_prefix}.csv",
                mime="text/csv",
                key=f"{key_prefix}_cf_csv",
            )

            invested = -sum(float(a) for _, a, _ in flows if float(a) < 0)
            withdrawn = sum(
                float(a) for _, a, lbl in flows if float(a) > 0 and lbl != "terminal_value"
            )
            terminal = sum(float(a) for _, a, lbl in flows if lbl == "terminal_value")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Flows", len(flows))
            m2.metric("Invested (outflows)", inr(invested))
            m3.metric("Withdrawals + dividends", inr(withdrawn))
            m4.metric("Terminal value", inr(terminal))

            by_label = {}
            for _, a, lbl in flows:
                by_label[lbl] = by_label.get(lbl, 0.0) + float(a)
            grp = pd.DataFrame([
                {"Flow": _XIRR_FLOW_LABELS.get(k, str(k)), "Net amount (Rs)": round(v, 2)}
                for k, v in sorted(by_label.items())
            ])
            ui.safe_dataframe(grp)

    # ---- 2. Timing vs selection decomposition ------------------------------
    twr_cum = None
    if ctx.txs:
        try:
            twr_cum = calculate_portfolio_twr(
                ctx.txs,
                actions=ctx.corporate_actions,
                dividends=ctx.dividend_flows,
                price_lookup=ctx.pl,
                start_date=start,
                end_date=end,
                opening_positions=ctx.opening_positions,
            )
        except Exception:  # noqa: BLE001
            twr_cum = None
    twr_ann = _annualize(twr_cum, days)

    nonterm = [(d, a) for (d, a, lbl) in flows if lbl != "terminal_value"]

    def _tri(d):
        try:
            from market_data.prices import get_index_tri
            return get_index_tri("NIFTY50TRI", str(d)[:10], db_path=ctx.market_db_path)
        except Exception:  # noqa: BLE001
            return None

    bench_xirr = None
    try:
        bre = calculate_benchmark_xirr(nonterm, _tri, start, end)
        bench_xirr = bre.get("xirr") if isinstance(bre, dict) else None
    except Exception:  # noqa: BLE001
        bench_xirr = None

    bench_ann = None
    tri_start = _tri(sd) if sd is not None else None
    tri_end = _tri(ed) if ed is not None else None
    if tri_start and tri_end:
        try:
            bench_ann = _annualize(float(tri_end) / float(tri_start) - 1.0, days)
        except (TypeError, ValueError, ZeroDivisionError):
            bench_ann = None

    port_num = _num(port_xirr)
    twr_num = _num(twr_ann)
    bench_num = _num(bench_ann)
    bench_xirr_num = _num(bench_xirr)
    timing = (port_num - twr_num) if (port_num is not None and twr_num is not None) else None
    selection_vs_index = (twr_num - bench_num) if (twr_num is not None and bench_num is not None) else None
    selection_vs_matched = (port_num - bench_xirr_num) if (port_num is not None and bench_xirr_num is not None) else None

    st.markdown("##### Timing vs selection")
    st.caption(
        "XIRR is money-weighted: it rewards putting money in before rallies and taking it "
        "out before drops. TWR strips that timing away, leaving stock selection."
    )
    decomp = pd.DataFrame([
        {"Metric": "Portfolio XIRR (money-weighted, annualised)", "Value": _pct(port_num)},
        {"Metric": "Portfolio TWR (time-weighted, annualised)", "Value": _pct(twr_num)},
        {"Metric": "Cash-flow timing effect (XIRR − TWR)", "Value": _pct(timing)},
        {"Metric": "Nifty 50 TRI (buy & hold, annualised)", "Value": _pct(bench_num)},
        {"Metric": "Selection vs index (TWR − Nifty TRI)", "Value": _pct(selection_vs_index)},
        {"Metric": "Benchmark matched XIRR (same cash flows)", "Value": _pct(bench_xirr_num)},
        {"Metric": "Selection vs benchmark (XIRR − matched XIRR)", "Value": _pct(selection_vs_matched)},
    ])
    ui.safe_dataframe(decomp)

    if timing is not None and abs(timing) >= 0.01:
        direction = "boosted" if timing > 0 else "dragged"
        st.caption(
            f"Cash-flow timing {direction} the return by {abs(timing) * 100:.1f}% p.a. "
            "versus a time-weighted view."
        )
    if selection_vs_matched is not None and abs(selection_vs_matched) >= 0.01:
        direction = "outperformed" if selection_vs_matched > 0 else "underperformed"
        st.caption(
            f"With identical cash-flow timing, the portfolio {direction} the Nifty 50 TRI "
            f"by {abs(selection_vs_matched) * 100:.1f}% p.a. (selection skill)."
        )

    # ---- 3. Risk-adjusted metrics ------------------------------------------
    st.markdown("##### Risk-adjusted metrics")
    try:
        series = portfolio_value_series(
            ctx.txs,
            price_lookup=ctx.pl,
            start_date=start,
            end_date=end,
            opening_positions=ctx.opening_positions,
        )
    except Exception:  # noqa: BLE001
        series = []

    if len(series) < 3:
        st.caption("Not enough monthly valuation points in this period to compute risk metrics.")
    else:
        rets = mf_analytics.periodic_returns(series)
        vol = mf_analytics.annualized_volatility(rets, periods_per_year=12)
        sharpe = mf_analytics.sharpe_ratio(rets, risk_free_annual=0.06, periods_per_year=12)
        sortino = mf_analytics.sortino_ratio(rets, risk_free_annual=0.06, periods_per_year=12)
        mdd = mf_analytics.max_drawdown(series)
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Annualised volatility", _pct(vol))
        r2.metric("Sharpe (rf 6%)", f"{sharpe:.2f}" if sharpe is not None else "N/A")
        r3.metric("Sortino (rf 6%)", f"{sortino:.2f}" if sortino is not None else "N/A")
        r4.metric("Max drawdown", _pct(mdd.get("max_drawdown")) if mdd else "N/A")
        if mdd:
            st.caption(
                f"Max drawdown peak {mdd.get('peak_date')} → trough {mdd.get('trough_date')}. "
                f"Monthly valuations: {len(series)} points."
            )
        st.caption(
            "Volatility/Sharpe/Sortino use month-end portfolio valuations (12 periods per year) "
            "with a 6% annual risk-free rate. They show how much risk produced the return, "
            "which XIRR alone does not capture."
        )


def _render_benchmark_xirr(ctx, res, start, end) -> None:
    st.markdown("#### Portfolio vs Benchmark")
    from market_data.prices import (
        get_benchmark_sync_status,
        get_index_tri,
        sync_benchmark_history,
    )
    from performance.benchmarks import calculate_benchmark_xirr

    md = ctx.market_db_path
    s50 = get_benchmark_sync_status("NIFTY50TRI", db_path=md)
    s500 = get_benchmark_sync_status("NIFTY500TRI", db_path=md)
    st.caption(
        "TRI cache - Nifty 50: " + str(s50.get("records") or 0) + " rows ("
        + str(s50.get("earliest")) + " to " + str(s50.get("latest")) + ") | Nifty 500: "
        + str(s500.get("records") or 0) + " rows (" + str(s500.get("earliest")) + " to "
        + str(s500.get("latest")) + ")"
    )
    _srcs = set(s50.get("sources") or []) | set(s500.get("sources") or [])
    if any("price_proxy" in str(x) for x in _srcs):
        st.caption(
            "Benchmark levels are Yahoo Finance price indices (^NSEI / ^CRSLDX) - "
            "a proxy for TRI (TRI includes dividends)."
        )
    elif any("nse_archive" in str(x) for x in _srcs):
        st.caption(
            "Benchmark levels are from NSE daily index archives "
            "(price index; TRI includes dividends)."
        )
    _tri_start = s50.get("earliest")
    if _tri_start and str(start)[:10] < str(_tri_start)[:10]:
        st.caption(
            "Benchmark comparison starts from " + str(_tri_start)
            + " (earliest available Nifty 50 level)."
        )
    if st.button("Sync Nifty 50/500 TRI for this period", key="sync_tri_portfolio"):
        errors = []
        with st.spinner("Syncing Nifty TRI data..."):
            for iid in ("NIFTY50TRI", "NIFTY500TRI"):
                try:
                    sync_benchmark_history(iid, str(start), str(end), db_path=md)
                except Exception as e:  # noqa: BLE001
                    errors.append(iid + ": " + str(e))
        for k in list(st.session_state.keys()):
            if str(k).startswith("bench_growth"):
                st.session_state.pop(k, None)
        if errors:
            st.warning("Sync issues: " + "; ".join(errors))
        else:
            st.success("Nifty 50/500 TRI synced.")
        st.rerun()

    if st.button("Fetch from NSE daily archive (slow)", key="sync_tri_archive"):
        counts = []
        errors = []
        with st.spinner("Downloading NSE daily index archives..."):
            for iid in ("NIFTY50TRI", "NIFTY500TRI"):
                try:
                    res = sync_benchmark_history(
                        iid, str(start), str(end), db_path=md,
                        use_archive=True, archive_max_requests=250,
                    )
                    counts.append(
                        iid + ": " + str(res.get("upserted", 0)) + " rows ("
                        + str(res.get("attempted", 0)) + " requests, source "
                        + str(res.get("source", "?")) + ")"
                    )
                except Exception as e:  # noqa: BLE001
                    errors.append(iid + ": " + str(e))
        for k in list(st.session_state.keys()):
            if str(k).startswith("bench_growth"):
                st.session_state.pop(k, None)
        if counts:
            st.info("; ".join(counts))
        if errors:
            st.warning("; ".join(errors))
        st.rerun()

    with st.expander("Import TRI CSV manually", expanded=False):
        csv_index = st.selectbox("Index", ["NIFTY50TRI", "NIFTY500TRI"], key="tri_csv_index")
        csv_file = st.file_uploader("CSV with date,level columns", type=["csv"], key="tri_csv_file")
        if csv_file is not None and st.button("Import TRI CSV", key="tri_csv_import"):
            import os as _os
            import tempfile as _tempfile
            fd, tmp = _tempfile.mkstemp(suffix=".csv")
            with _os.fdopen(fd, "wb") as fh:
                fh.write(csv_file.getbuffer())
            try:
                res = sync_benchmark_history(csv_index, str(start), str(end), db_path=md, path=tmp)
                for k in list(st.session_state.keys()):
                    if str(k).startswith("bench_growth"):
                        st.session_state.pop(k, None)
                st.success(
                    "Imported " + str(res.get("upserted", res.get("rows", 0)))
                    + " rows for " + csv_index
                )
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error("CSV import failed: " + str(e))
            finally:
                try:
                    _os.remove(tmp)
                except Exception:  # noqa: BLE001, S110
                    pass

    def _idx(iid):
        def _f(d):
            try:
                return get_index_tri(iid, str(d)[:10], db_path=md)
            except Exception:  # noqa: BLE001
                return None
        return _f

    nonterm = [(d, a) for (d, a, lbl) in (res.get("cashflows") or []) if lbl != "terminal_value"]
    b50 = None
    b500 = None
    try:
        b50 = calculate_benchmark_xirr(nonterm, _idx("NIFTY50TRI"), start, end).get("xirr")
        b500 = calculate_benchmark_xirr(nonterm, _idx("NIFTY500TRI"), start, end).get("xirr")
    except Exception as e:  # noqa: BLE001
        st.caption("Benchmark comparison unavailable: " + str(e))

    port = res.get("xirr")
    if b50 is None and b500 is None:
        st.caption(
            "Benchmark TRI data not available for this period. Use the Sync button above, "
            "or import a TRI CSV in Data & Settings."
        )
        return
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Portfolio XIRR", fmt_pct(port) if port is not None else "N/A")
    c2.metric("Nifty 50 matched", fmt_pct(b50) if b50 is not None else "N/A")
    c3.metric("Nifty 500 matched", fmt_pct(b500) if b500 is not None else "N/A")
    c4.metric(
        "Excess vs 50",
        fmt_pct(port - b50) if (port is not None and b50 is not None) else "N/A",
    )
    c5.metric(
        "Excess vs 500",
        fmt_pct(port - b500) if (port is not None and b500 is not None) else "N/A",
    )

    _render_benchmark_growth(ctx, start, end)
