"""Portfolio copilot tools — wrap the existing deterministic engines (read-only)."""
from __future__ import annotations

from copilot.context import CopilotContext
from copilot.tools.base import ToolSpec, register
from copilot.types import TIER_ESTIMATE, TIER_FACT, Citation, ToolResult


def _inr(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if abs(v) >= 1e7:
        return f"₹{v/1e7:.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v/1e5:.2f} L"
    return f"₹{v:,.0f}"


def _pct(v) -> str:
    try:
        return f"{float(v)*100:.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _equity_invested(holdings_df) -> float:
    if holdings_df is None or getattr(holdings_df, "empty", True):
        return 0.0
    total = 0.0
    for _, r in holdings_df.iterrows():
        try:
            total += float(r.get("quantity") or 0) * float(r.get("avg_price") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _portfolio_summary(args: dict, ctx: CopilotContext) -> ToolResult:
    from performance.xirr import calculate_portfolio_xirr
    from portfolio import allocation

    warnings: list[str] = []
    citations: list[Citation] = []
    xirr = None
    mf = ctx.mf or {}
    mf_value = float(mf.get("total_value") or 0) if mf else 0.0
    mf_invested = float(mf.get("total_invested") or 0) if mf else 0.0
    invested = _equity_invested(ctx.holdings_df) + mf_invested
    if ctx.txs:
        try:
            res = calculate_portfolio_xirr(ctx.txs, end_date=ctx.end_date, price_lookup=ctx.price_lookup, holdings_at_end=_equity_qty_map(ctx))
            xirr = res.get("xirr")
            if xirr is None and res.get("reason"):
                warnings.append(f"Portfolio XIRR unavailable: {res['reason']} (missing prices/coverage).")
            citations.append(Citation("performance.xirr", "calculate_portfolio_xirr", str(ctx.end_date)))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"XIRR computation failed: {exc}")
    else:
        warnings.append("No equity transactions available for XIRR.")
    report = allocation.allocation_report(ctx.positions, ctx.targets)
    citations.append(Citation("portfolio.allocation", "allocation_report", str(ctx.end_date)))
    if report.get("breached"):
        warnings.append("Allocation outside configured bounds: " + ", ".join(report["breached"]) + ".")
    gains = ctx.total_value - invested if invested > 0 else 0.0
    data = {
        "total_value": round(ctx.total_value, 2),
        "equity_value": round(ctx.total_value - mf_value, 2),
        "mf_value": round(mf_value, 2),
        "invested": round(invested, 2),
        "gains": round(gains, 2),
        "xirr": xirr,
        "holdings_count": len(ctx.positions),
        "breached_classes": report.get("breached", []),
    }
    summary = (
        f"Total portfolio value {_inr(ctx.total_value)} across {len(ctx.positions)} holdings "
        f"(equity {_inr(ctx.total_value - mf_value)}, MF {_inr(mf_value)}); "
        f"invested {_inr(invested)}, gains {_inr(gains)}; portfolio XIRR "
        f"{_pct(xirr) if xirr is not None else 'unavailable'}."
    )
    return ToolResult("portfolio_summary", True, summary, data, citations, warnings, TIER_FACT)


def _holdings(args: dict, ctx: CopilotContext) -> ToolResult:
    n = int(args.get("limit", 10))
    rows = sorted(ctx.positions, key=lambda p: float(p.get("value") or 0), reverse=True)[:n]
    data = {
        "holdings": [
            {"security_id": p["security_id"], "name": p.get("name"), "value": round(float(p.get("value") or 0), 2),
             "weight_pct": round(ctx.weight_pct(p["security_id"]), 2), "asset_class": p.get("asset_class")}
            for p in rows
        ],
        "total_value": round(ctx.total_value, 2),
    }
    top = data["holdings"][0] if data["holdings"] else None
    summary = (
        f"Top {len(data['holdings'])} holdings of {len(ctx.positions)}. "
        + (f"Largest: {top['name']} at {_inr(top['value'])} ({top['weight_pct']:.1f}%)." if top else "No holdings.")
    )
    return ToolResult("holdings", True, summary, data,
                      [Citation("holdings statement", "current_value", str(ctx.end_date))], [], TIER_FACT)


def _xirr(args: dict, ctx: CopilotContext) -> ToolResult:
    from performance.xirr import calculate_portfolio_xirr, calculate_stock_xirr

    warnings: list[str] = []
    token = args.get("security")
    sid = ctx.resolve_security(token) if token else None
    if token and not sid:
        return ToolResult("xirr", False, f"Could not find a holding matching '{token}'.",
                          {"security": token}, [], [f"Unknown security '{token}'."], TIER_FACT)
    if not ctx.txs:
        return ToolResult("xirr", False, "No equity transactions available to compute XIRR.",
                          {}, [], ["No transactions imported."], TIER_FACT)
    if sid:
        res = calculate_stock_xirr(ctx.txs, security_id=sid, end_date=ctx.end_date, price_lookup=ctx.price_lookup)
    else:
        res = calculate_portfolio_xirr(ctx.txs, end_date=ctx.end_date, price_lookup=ctx.price_lookup, holdings_at_end=_equity_qty_map(ctx))
    xirr = res.get("xirr")
    if xirr is None and res.get("reason"):
        warnings.append(f"XIRR unavailable: {res['reason']}.")
    label = ctx.names.get(sid, sid) if sid else "the portfolio"
    data = {"security_id": sid, "label": label, "xirr": xirr, "reason": res.get("reason"),
            "cashflows": len(res.get("cashflows", []))}
    summary = f"{str(label).capitalize()} XIRR is {_pct(xirr)}" if xirr is not None else f"XIRR for {label} is unavailable."
    return ToolResult("xirr", True, summary, data,
                      [Citation("performance.xirr", "calculate_stock_xirr" if sid else "calculate_portfolio_xirr",
                                str(ctx.end_date))], warnings, TIER_FACT)


register(ToolSpec("portfolio_summary", "Portfolio value, invested, gains and XIRR",
                  ("portfolio", "summary", "value", "net worth", "overview"), _portfolio_summary))
register(ToolSpec("holdings", "Top holdings with weights", ("holdings", "top holdings", "largest"),
                  _holdings, {"limit": {"type": "int", "min": 1, "max": 50}}))
register(ToolSpec("xirr", "Portfolio or per-security XIRR",
                  ("xirr", "return", "cagr", "performance"), _xirr,
                  {"security": {"type": "str"}}))



def _allocation(args: dict, ctx: CopilotContext) -> ToolResult:
    from portfolio import allocation

    rep = allocation.allocation_report(ctx.positions, ctx.targets)
    rows = rep.get("rows", [])
    data = {
        "rows": [{k: r.get(k) for k in ("asset_class", "current_pct", "target_pct", "drift_pct", "breached")} for r in rows],
        "breached": rep.get("breached", []),
        "mf_direct_split": rep.get("mf_direct_split", {}),
        "total": rep.get("total"),
    }
    parts = [f"{r['asset_class']} {r['current_pct']:.1f}% vs target {r['target_pct']:.0f}%" for r in rows]
    warnings = [f"{c} outside configured bounds." for c in rep.get("breached", [])]
    summary = "Allocation: " + "; ".join(parts) if parts else "No allocation data available."
    return ToolResult("allocation", True, summary, data,
                      [Citation("portfolio.allocation", "allocation_report", str(ctx.end_date))], warnings, TIER_FACT)


def _concentration(args: dict, ctx: CopilotContext) -> ToolResult:
    from portfolio import allocation

    metrics = allocation.concentration_metrics(ctx.positions)
    top = sorted(ctx.positions, key=lambda p: float(p.get("value") or 0), reverse=True)[:5]
    contributors = [{"security_id": p["security_id"], "name": p.get("name"),
                     "weight_pct": round(ctx.weight_pct(p["security_id"]), 2)} for p in top]
    warnings = []
    if metrics.get("top_pct") and metrics["top_pct"] > 25:
        warnings.append(f"Largest holding is {metrics['top_pct']:.1f}% of the portfolio (>25%).")
    if metrics.get("hhi") and metrics["hhi"] > 0.25:
        warnings.append("High concentration (HHI > 0.25) — portfolio is undiversified.")
    data = {"hhi": metrics.get("hhi"), "top_pct": metrics.get("top_pct"),
            "n_positions": metrics.get("n_positions"), "top_contributors": contributors}
    summary = (f"Concentration HHI {metrics.get('hhi')}, largest holding {metrics.get('top_pct')}% "
               f"of {metrics.get('n_positions')} positions.")
    return ToolResult("concentration", True, summary, data,
                      [Citation("portfolio.allocation", "concentration_metrics")], warnings, TIER_FACT)


def _gains(args: dict, ctx: CopilotContext) -> ToolResult:
    rows = []
    total_ug = 0.0
    for p in ctx.positions:
        if p.get("is_mf"):
            continue
        try:
            q = float(p.get("quantity") or 0)
            avg = float(p.get("avg_price") or 0)
        except (TypeError, ValueError):
            q = avg = 0.0
        cost = q * avg
        val = float(p.get("value") or 0)
        ug = val - cost
        total_ug += ug
        rows.append({"security_id": p["security_id"], "name": p.get("name"),
                     "invested": round(cost, 2), "value": round(val, 2), "unrealized_gain": round(ug, 2)})
    rows.sort(key=lambda r: r["unrealized_gain"], reverse=True)
    warnings = ["Realized gains require the full FIFO ledger; showing unrealized gains from the holdings statement."]
    data = {"unrealized_total": round(total_ug, 2), "by_holding": rows}
    summary = f"Unrealized gain across equity holdings: {_inr(total_ug)}."
    return ToolResult("gains", True, summary, data,
                      [Citation("holdings statement", "current_value − quantity×avg_price")], warnings, TIER_FACT)


def _dividends(args: dict, ctx: CopilotContext) -> ToolResult:
    warnings: list[str] = []
    try:
        from market_data.repository import MarketDataStore
        store = MarketDataStore(ctx.app_ctx.market_db_path)
        events = store.get_dividend_events()
    except Exception as exc:  # noqa: BLE001
        return ToolResult("dividends", False, "Dividend data is unavailable.",
                          {}, [], [f"Could not load dividend events: {exc}"], TIER_FACT)
    token = args.get("security")
    sid = ctx.resolve_security(token) if token else None
    rows = []
    total_est = 0.0
    for e in events or []:
        esid = str(e.get("security_id") or "").strip().upper()
        if sid and esid != sid:
            continue
        dps = e.get("dividend_per_share")
        pos = ctx.position(esid)
        qty = float(pos.get("quantity") or 0) if pos else 0.0
        est = float(dps) * qty if (dps is not None and qty > 0) else None
        if est:
            total_est += est
        rows.append({"security_id": esid, "ex_date": e.get("ex_date"),
                     "dividend_per_share": dps, "estimated_gross": round(est, 2) if est else None})
    if not rows:
        warnings.append("No verified dividend events found for this selection.")
    data = {"events": rows[:20], "count": len(rows), "estimated_gross_total": round(total_est, 2)}
    summary = f"{len(rows)} dividend event(s)" + (f"; estimated gross {_inr(total_est)} at current quantities." if total_est else ".")
    return ToolResult("dividends", True, summary, data,
                      [Citation("market_data.repository", "get_dividend_events")], warnings, TIER_ESTIMATE)


register(ToolSpec("allocation", "Asset allocation vs targets", ("allocation", "asset allocation", "drift"),
                  _allocation))
register(ToolSpec("concentration", "Concentration / biggest contributors",
                  ("concentration", "contribut", "hhi", "risk", "diversif"), _concentration))
register(ToolSpec("gains", "Realized/unrealized gains", ("gain", "profit", "unrealized", "p&l"), _gains))
register(ToolSpec("dividends", "Dividend history", ("dividend", "idcw", "payout"), _dividends,
                  {"security": {"type": "str"}}))



def _equity_qty_map(ctx) -> dict:
    out = {}
    for p in (ctx.positions or []):
        if p.get("is_mf"):
            continue
        sid = str(p.get("security_id") or "").strip().upper()
        if not sid:
            continue
        try:
            out[sid] = float(p.get("quantity") or 0)
        except (TypeError, ValueError):
            out[sid] = 0.0
    return out
