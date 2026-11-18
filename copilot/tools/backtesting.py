"""Backtesting copilot tool — stock replacement (reuses Stage 5 / Stage 11 engines)."""
from __future__ import annotations

from copilot.context import CopilotContext
from copilot.tools.base import ToolSpec, register
from copilot.types import TIER_ESTIMATE, TIER_FACT, Citation, ToolResult


def _pct(v) -> str:
    try:
        return f"{float(v)*100:.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _normalize_equity_txs(txs: list[dict]) -> list[dict]:
    """Map equity statement fields to the backtest engine's expected keys."""
    out = []
    for t in txs or []:
        d = dict(t)
        if not d.get("side") and d.get("buy_sell"):
            d["side"] = str(d["buy_sell"])
        if d.get("units") in (None, 0) and d.get("quantity") is not None:
            d["units"] = d["quantity"]
        if d.get("amount") in (None, 0) and d.get("net_amount") is not None:
            d["amount"] = d["net_amount"]
        out.append(d)
    return out


def _replacement_simulation(args: dict, ctx: CopilotContext) -> ToolResult:
    from mf.backtesting import calculate_backtest_xirr, simulate_stock_replacement

    a_tok, b_tok = args.get("security_a"), args.get("security_b")
    sid_a = ctx.resolve_security(a_tok)
    sid_b = ctx.resolve_security(b_tok)
    if not sid_a or not sid_b:
        missing = a_tok if not sid_a else b_tok
        return ToolResult("replacement_simulation", False,
                          f"Could not find a holding matching '{missing}'.",
                          {"security_a": a_tok, "security_b": b_tok}, [],
                          [f"Unknown security '{missing}'."], TIER_FACT)
    if sid_a == sid_b:
        return ToolResult("replacement_simulation", False, "Replacement must differ from the original.",
                          {}, [], ["Same security selected for A and B."], TIER_FACT)
    if not ctx.txs:
        return ToolResult("replacement_simulation", False, "No transactions available for backtesting.",
                          {}, [], ["No transactions imported."], TIER_FACT)
    full = bool(args.get("full", True))
    norm_txs = _normalize_equity_txs(ctx.txs)
    sim = simulate_stock_replacement(norm_txs, sid_a, sid_b, str(ctx.end_date), is_full_replacement=full)
    if sim.replacement_info.get("error"):
        return ToolResult("replacement_simulation", False, sim.replacement_info["error"],
                          {"replacement_info": sim.replacement_info}, [],
                          [sim.replacement_info["error"]], TIER_FACT)
    hypothetical = list(sim.preserved_transactions) + list(sim.replaced_transactions)
    actual = calculate_backtest_xirr(norm_txs, str(ctx.end_date), ctx.price_lookup)
    hyp = calculate_backtest_xirr(hypothetical, str(ctx.end_date), ctx.price_lookup)
    a_x, h_x = actual.get("xirr"), hyp.get("xirr")
    diff = (h_x - a_x) if (a_x is not None and h_x is not None) else None
    warnings = []
    if actual.get("missing_data") or hyp.get("missing_data"):
        warnings.append("Missing price data for some positions; backtest values may be provisional.")
    data = {
        "original": sid_a, "replacement": sid_b, "full_replacement": full,
        "actual_xirr": a_x, "hypothetical_xirr": h_x, "xirr_difference": diff,
        "actual_terminal": actual.get("total_terminal"), "hypothetical_terminal": hyp.get("total_terminal"),
        "actual_invested": actual.get("total_invested"), "hypothetical_invested": hyp.get("total_invested"),
        "replaced_transactions": sim.replacement_info.get("replaced_count"),
    }
    summary = (
        f"Replacing {ctx.names.get(sid_a, sid_a)} with {ctx.names.get(sid_b, sid_b)}: "
        f"portfolio XIRR {_pct(a_x)} → {_pct(h_x)} (difference {_pct(diff) if diff is not None else 'N/A'})."
    )
    return ToolResult("replacement_simulation", True, summary, data,
                      [Citation("mf.backtesting", "simulate_stock_replacement + calculate_backtest_xirr",
                                str(ctx.end_date))], warnings, TIER_ESTIMATE)


register(ToolSpec("replacement_simulation", "What-if: replace stock A with stock B",
                  ("replace", "instead of", "swap stock", "replacement"),
                  _replacement_simulation,
                  {"security_a": {"type": "str", "required": True},
                   "security_b": {"type": "str", "required": True},
                   "full": {"type": "bool"}}))
