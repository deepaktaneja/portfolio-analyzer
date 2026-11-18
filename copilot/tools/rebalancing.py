"""Rebalancing & scenario copilot tools — reuse Stage 9 engines (read-only)."""
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


def _lots_by_security(ctx: CopilotContext) -> dict:
    from portfolio.tax_lots import build_fifo_lots

    grouped: dict[str, list] = {}
    for t in ctx.txs or []:
        sid = str(t.get("isin") or "").strip().upper()
        if sid:
            grouped.setdefault(sid, []).append(t)
    return {sid: build_fifo_lots(txs, sid) for sid, txs in grouped.items()}


def _rebalance_plan(args: dict, ctx: CopilotContext) -> ToolResult:
    from portfolio.rebalancer import build_execution_plan

    cash = float(args.get("cash", 0) or 0)
    if not ctx.positions:
        return ToolResult("rebalance_plan", False, "No positions available to rebalance.",
                          {}, [], ["No holdings imported."], TIER_FACT)
    result = build_execution_plan(
        ctx.positions, ctx.targets, cash_available=cash, lots_by_security=_lots_by_security(ctx),
        sell_date=str(ctx.end_date),
    )
    plan = result.get("plan", {})
    actions = plan.get("actions", [])
    warnings = list(plan.get("warnings") or [])
    if plan.get("missing_cost_basis"):
        warnings.append("Some holdings lack cost basis — tax is flagged as an estimate.")
    if plan.get("protected_compounders_skipped"):
        warnings.append("Protected compounders not sold: " + ", ".join(plan["protected_compounders_skipped"]))
    data = {
        "cash_available": cash,
        "actions": actions,
        "cash_used": plan.get("cash_used"),
        "sells_value": plan.get("sells_value"),
        "buys_value": plan.get("buys_value"),
        "estimated_tax": plan.get("estimated_tax"),
        "turnover_pct": plan.get("turnover_pct"),
        "rebalance_due": result.get("rebalance_due"),
        "due_reason": result.get("due_reason"),
        "drift_after": plan.get("drift_after"),
    }
    n_sell = sum(1 for a in actions if a.get("action") == "SELL")
    n_buy = sum(1 for a in actions if a.get("action") == "BUY")
    summary = (
        f"Rebalance plan with {_inr(cash)} new cash: {n_sell} sell(s) {_inr(plan.get('sells_value'))}, "
        f"{n_buy} buy(s) {_inr(plan.get('buys_value'))}, estimated tax {_inr(plan.get('estimated_tax'))}, "
        f"turnover {plan.get('turnover_pct')}%."
    )
    return ToolResult("rebalance_plan", True, summary, data,
                      [Citation("portfolio.rebalancer", "build_execution_plan", str(ctx.end_date)),
                       Citation("portfolio.optimizer", "plan_rebalance")], warnings, TIER_ESTIMATE)


register(ToolSpec("rebalance_plan", "Tax-aware rebalance plan with new cash",
                  ("rebalance", "new investment", "invest", "allocate cash", "deploy"),
                  _rebalance_plan,
                  {"cash": {"type": "float", "min": 0, "max": 1e12}}))


def _trim_simulation(args: dict, ctx: CopilotContext) -> ToolResult:
    from portfolio import allocation

    token = args.get("security")
    pct = float(args.get("pct", 0) or 0)
    sid = ctx.resolve_security(token) if token else None
    if not sid:
        return ToolResult("trim_simulation", False, f"Could not find a holding matching '{token}'.",
                          {"security": token}, [], [f"Unknown security '{token}'."], TIER_FACT)
    if not (0 < pct <= 100):
        return ToolResult("trim_simulation", False, "Trim percentage must be between 1 and 100.",
                          {"pct": pct}, [], ["Invalid trim percentage."], TIER_FACT)
    pos = ctx.position(sid)
    value = float(pos.get("value") or 0)
    trim_amt = value * pct / 100.0
    before_w = ctx.weight_pct(sid)
    before = allocation.concentration_metrics(ctx.positions)
    adjusted = []
    for p in ctx.positions:
        p2 = dict(p)
        if p2.get("security_id") == sid:
            p2["value"] = max(0.0, float(p2.get("value") or 0) - trim_amt)
        adjusted.append(p2)
    after = allocation.concentration_metrics(adjusted)
    total = ctx.total_value or 1.0
    after_w = (value - trim_amt) / total * 100.0
    data = {
        "security_id": sid, "name": ctx.names.get(sid, sid), "trim_pct": pct,
        "current_value": round(value, 2), "trim_proceeds": round(trim_amt, 2),
        "remaining_value": round(value - trim_amt, 2),
        "weight_before_pct": round(before_w, 2), "weight_after_pct": round(after_w, 2),
        "hhi_before": before.get("hhi"), "hhi_after": after.get("hhi"),
        "top_pct_before": before.get("top_pct"), "top_pct_after": after.get("top_pct"),
    }
    warnings = ["Simulation assumes proceeds are held as cash (portfolio value unchanged); no tax computed here."]
    summary = (
        f"Trimming {pct:.0f}% of {ctx.names.get(sid, sid)} sells {_inr(trim_amt)}; weight falls from "
        f"{before_w:.1f}% to {after_w:.1f}%, HHI {before.get('hhi')} → {after.get('hhi')}."
    )
    return ToolResult("trim_simulation", True, summary, data,
                      [Citation("portfolio.allocation", "concentration_metrics (before/after trim)")],
                      warnings, TIER_ESTIMATE)


def _overlap(args: dict, ctx: CopilotContext) -> ToolResult:
    pairs = (ctx.mf or {}).get("overlap", {}).get("pairs", []) if ctx.mf else []
    if not pairs:
        return ToolResult("overlap", False, "No mutual-fund overlap data available.",
                          {}, [], ["Import MF statements + scheme holdings disclosures to compute overlap."], TIER_FACT)
    ranked = sorted(pairs, key=lambda p: float(p.get("weight_overlap_pct") or 0), reverse=True)[:5]
    data = {"pairs": [{k: p.get(k) for k in ("scheme_a", "scheme_b", "common_count", "jaccard", "weight_overlap_pct")}
                      for p in ranked]}
    top = ranked[0]
    summary = (
        f"{len(pairs)} fund pair(s) compared; highest overlap {top['scheme_a']} ↔ {top['scheme_b']} at "
        f"{top['weight_overlap_pct']:.1f}% weight overlap."
    )
    warnings = []
    if float(top.get("weight_overlap_pct") or 0) >= 40:
        warnings.append("High overlap between the top pair — consider consolidating to reduce duplication.")
    return ToolResult("overlap", True, summary, data,
                      [Citation("mf.overlap", "overlap_matrix")], warnings, TIER_FACT)


register(ToolSpec("trim_simulation", "Impact of trimming a percentage of a holding",
                  ("trim", "sell some", "reduce position", "cut position"),
                  _trim_simulation,
                  {"security": {"type": "str", "required": True},
                   "pct": {"type": "float", "required": True, "min": 1, "max": 100}}))
register(ToolSpec("overlap", "Mutual fund overlap analysis",
                  ("overlap", "duplicate funds", "fund duplication"), _overlap))

