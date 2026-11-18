"""Research copilot tool — reuse Stage 6 orchestrator + cached reports (read-only).

Never fabricates research: if no cached report exists and no LLM provider is
configured, it returns an explicit 'research unavailable' result with a warning.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from copilot.context import CopilotContext
from copilot.tools.base import ToolSpec, register
from copilot.types import TIER_AI, TIER_FACT, Citation, ToolResult

FRESH_DAYS = 7


def _llm_available() -> bool:
    try:
        from config.settings import get_settings
        s = get_settings()
        return bool(s.gemini_api_key or s.openrouter_api_key)
    except Exception:  # noqa: BLE001
        return False


def load_cached_report(security_id: str, max_age_days: int = FRESH_DAYS) -> dict | None:
    """Load a recent stored research report for a security (if fresh), else None."""
    try:
        from models.research import ResearchReport, get_research_session
        s = get_research_session()
    except Exception:  # noqa: BLE001
        return None
    try:
        key = str(security_id).strip()
        rows = (s.query(ResearchReport)
                .filter(ResearchReport.security_id == key)
                .order_by(ResearchReport.generated_at.desc())
                .limit(1).all())
        if not rows:
            return None
        r = rows[0]
        try:
            generated = datetime.fromisoformat(r.generated_at)
            if generated.tzinfo is None:
                generated = generated.replace(tzinfo=UTC)
            if datetime.now(UTC) - generated > timedelta(days=max_age_days):
                return None
        except ValueError:
            pass
        return {"report_md": r.content_md, "title": r.title, "model": r.model,
                "generated_at": r.generated_at, "evidence_ids": r.evidence_ids}
    except Exception:  # noqa: BLE001
        return None
    finally:
        s.close()


def _generate_report(symbol: str, isin: str | None, portfolio_context: dict | None) -> dict:
    """Run the Stage-6 orchestrator (MCP evidence -> LLM report). Patchable in tests."""
    from research.orchestrator import ResearchOrchestrator

    orch = ResearchOrchestrator()
    coro = orch.generate_report(symbol=symbol, isin=isin, portfolio_context=portfolio_context)
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _research_report(args: dict, ctx: CopilotContext) -> ToolResult:
    token = str(args.get("security") or "").strip()
    if not token:
        return ToolResult("research_report", False, "Please specify a security to research.",
                          {}, [], ["Missing security symbol."], TIER_FACT)
    sid = ctx.resolve_security(token)
    symbol = (ctx.names.get(sid, sid) if sid else token).strip()
    isin = sid if sid else None
    warnings: list[str] = []
    citations: list[Citation] = []

    cached = load_cached_report(isin or symbol)
    if cached:
        data = {"security": symbol, "from_cache": True, "report_md": cached["report_md"],
                "generated_at": cached["generated_at"], "evidence_ids": cached["evidence_ids"]}
        citations.append(Citation("research_reports (cache)", cached.get("title", "stored report"),
                                  cached.get("generated_at")))
        return ToolResult("research_report", True,
                          f"Research (cached, generated {cached['generated_at']}) for {symbol}.",
                          data, citations, warnings, TIER_AI)

    if not _llm_available():
        return ToolResult("research_report", False,
                          f"Live research for {symbol} is unavailable: no LLM provider is configured.",
                          {"security": symbol, "from_cache": False},
                          [Citation("research.orchestrator", "unavailable")],
                          [("No LLM provider configured and no fresh cached report — research not generated "
                            "(no data invented).")], TIER_FACT)

    pos = ctx.position(sid) if sid else None
    portfolio_context = {"weight_pct": round(ctx.weight_pct(sid), 2)} if pos else None
    try:
        report = _generate_report(symbol, isin, portfolio_context)
    except Exception as exc:  # noqa: BLE001
        return ToolResult("research_report", False, f"Research generation failed for {symbol}.",
                          {"security": symbol}, [Citation("research.orchestrator", "error")],
                          [f"Research error: {exc}"], TIER_FACT)
    if not report.get("is_llm_available", True):
        warnings.append("LLM unavailable during generation; report may be partial.")
    for err in (report.get("mcp_errors") or [])[:5]:
        warnings.append(f"MCP data gap: {err}")
    data = {"security": symbol, "from_cache": False, "report_md": report.get("report_md", ""),
            "generated_at": report.get("generated_at"), "evidence_ids": report.get("evidence_ids", []),
            "mcp_errors": report.get("mcp_errors", [])}
    citations.append(Citation("research.orchestrator", f"report ({report.get('llm_provider')})",
                              report.get("generated_at")))
    summary = f"AI research report generated for {symbol} with {len(data['evidence_ids'])} cited evidence item(s)."
    return ToolResult("research_report", True, summary, data, citations, warnings, TIER_AI)


register(ToolSpec("research_report", "MCP-powered AI research for a security (cached-first)",
                  ("research", "valuation", "earnings", "governance", "analyst", "fundamentals", "outlook"),
                  _research_report, {"security": {"type": "str", "required": True}}))
