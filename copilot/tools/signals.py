"""Signal/decision copilot tools — reuse the Stage 8 decision engine (read-only)."""
from __future__ import annotations

from copilot.context import CopilotContext
from copilot.tools.base import ToolSpec, register
from copilot.types import TIER_FACT, Citation, ToolResult

_SELL = {"TRIM_REVIEW", "EXIT_REVIEW", "REPLACEMENT_REVIEW"}


def _price_gain_6m(market_db: str, security_id: str) -> float | None:
    try:
        from datetime import date, timedelta

        from market_data.prices import get_stock_prices
        end = date.today()
        start = end - timedelta(days=183)
        rows = get_stock_prices(security_id, start.isoformat(), end.isoformat(), db_path=market_db)
        if len(rows) >= 2 and rows[0][1] and rows[0][1] > 0:
            return rows[-1][1] / rows[0][1] - 1.0
    except Exception:  # noqa: BLE001
        return None
    return None


def build_recommendations(ctx: CopilotContext) -> list[dict]:
    """Compute decision recommendations for the current snapshot (pure, no Streamlit)."""
    from decision.engine import DecisionEngine

    engine = DecisionEngine()
    recs: list[dict] = []
    total = ctx.total_value
    for p in ctx.positions:
        if p.get("is_mf"):
            continue
        sid = p["security_id"]
        holding = {
            "security_id": sid,
            "weight_pct": (float(p.get("value") or 0) / total * 100) if total > 0 else 0.0,
            "portfolio_value": total,
            "price_gain_6m": _price_gain_6m(ctx.app_ctx.market_db_path, sid),
        }
        rec = engine.evaluate_equity(holding, research={}, base_case_cagr=None)
        rec["security_name"] = p.get("name") or sid
        recs.append(rec)
    if ctx.mf:
        overlap_max: dict[str, float] = {}
        jac_max: dict[str, float] = {}
        for pair in (ctx.mf.get("overlap", {}) or {}).get("pairs", []):
            for key in ("scheme_a", "scheme_b"):
                code = pair.get(key)
                overlap_max[code] = max(overlap_max.get(code, 0.0), float(pair.get("weight_overlap_pct") or 0))
                jac_max[code] = max(jac_max.get(code, 0.0), float(pair.get("jaccard") or 0))
        for row in ctx.mf.get("scheme_rows", []):
            code = row.get("scheme_code")
            metrics = {
                "scheme_code": code, "scheme_name": row.get("scheme_name"),
                "analytics": row.get("analytics", {}), "expense_ratio": row.get("expense_ratio"),
                "top5_pct": row.get("top5_pct"), "single_stock_pct": row.get("single_stock_pct"),
            }
            rec = engine.evaluate_mf(metrics, {"weight_overlap_pct": overlap_max.get(code),
                                               "jaccard": jac_max.get(code)})
            rec["security_name"] = row.get("scheme_name") or code
            recs.append(rec)
    return recs


def _signals(args: dict, ctx: CopilotContext) -> ToolResult:
    if not ctx.positions:
        return ToolResult("signals", False, "No holdings available for signal evaluation.",
                          {}, [], ["No holdings imported."], TIER_FACT)
    recs = build_recommendations(ctx)
    rows = [{"security_id": r.get("security_id"), "name": r.get("security_name"),
             "action": r.get("action"), "confidence": r.get("confidence"),
             "reasoning": r.get("reasoning"),
             "critical": (r.get("signal_counts") or {}).get("critical", 0),
             "warn": (r.get("signal_counts") or {}).get("warn", 0)} for r in recs]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    data = {"recommendations": rows, "counts_by_action": counts}
    summary = ("Signal scan (shadow mode): "
               + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ".")
    warnings = ["Advisory only — shadow mode, no trades executed."]
    return ToolResult("signals", True, summary, data,
                      [Citation("decision.engine", "evaluate_equity/evaluate_mf", str(ctx.end_date))],
                      warnings, TIER_FACT)


def _explain_signal(args: dict, ctx: CopilotContext) -> ToolResult:
    token = args.get("security")
    sid = ctx.resolve_security(token)
    if not sid:
        return ToolResult("explain_signal", False, f"Could not find a holding matching '{token}'.",
                          {"security": token}, [], [f"Unknown security '{token}'."], TIER_FACT)
    rec = next((r for r in build_recommendations(ctx) if str(r.get("security_id")).upper() == sid), None)
    if rec is None:
        return ToolResult("explain_signal", False, f"No recommendation for {token}.",
                          {}, [], [f"No signal generated for '{token}'."], TIER_FACT)
    data = {
        "security_id": rec.get("security_id"), "name": rec.get("security_name"),
        "action": rec.get("action"), "confidence": rec.get("confidence"),
        "reasoning": rec.get("reasoning"), "signals": rec.get("signals", []),
        "tiers": rec.get("tiers", {}), "tax": rec.get("tax"),
        "evidence_ids": rec.get("evidence_ids", []),
    }
    sig_types = ", ".join(sorted({s.get("signal_type", "") for s in rec.get("signals", [])})) or "none"
    summary = (f"{rec.get('security_name') or sid}: {rec.get('action')} "
               f"(confidence {rec.get('confidence')}). Triggers: {sig_types}.")
    warnings = [] if rec.get("action") not in _SELL else ["Review is advisory only — no trade executed."]
    return ToolResult("explain_signal", True, summary, data,
                      [Citation("decision.engine", "recommendation + evidence", str(rec.get("generated_at")))],
                      warnings, TIER_FACT)


register(ToolSpec("signals", "HOLD/MONITOR/TRIM/EXIT/REPLACEMENT signals",
                  ("signal", "trim review", "exit review", "hold", "replacement review", "what should i"),
                  _signals))
register(ToolSpec("explain_signal", "Explain a specific signal with supporting evidence",
                  ("why trim", "why exit", "explain signal", "why hold", "reason for"),
                  _explain_signal, {"security": {"type": "str", "required": True}}))

