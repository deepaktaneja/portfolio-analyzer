"""Copilot engine: sanitise -> route intent -> validate -> run tool -> build answer.

The engine never performs financial calculations itself: every figure comes from a
registered deterministic tool that wraps an existing engine. The LLM (optional) is used
only for intent routing fallback and for phrasing an explanation around tool facts.
"""
from __future__ import annotations

from app.context import AppContext
from copilot.config import load_copilot_config
from copilot.context import CopilotContext, build_copilot_context
from copilot.intent import Intent, classify, classify_with_llm
from copilot.response import build_answer, narrate
from copilot.security import AuditLogger, RateLimiter, authorize, sanitise_user_input, validate_args
from copilot.tools import get_tool
from copilot.types import CopilotResponse, ToolResult


def _resolve_tool_name(intent: Intent, allowed: set[str]) -> str:
    if intent.tool in allowed:
        return intent.tool
    if "portfolio_summary" in allowed:
        return "portfolio_summary"
    return min(allowed) if allowed else ""


class CopilotEngine:
    """Conversational orchestration over deterministic portfolio engines."""

    def __init__(self, config: dict | None = None, llm_client=None, default_role: str = "viewer"):
        self.config = config or load_copilot_config()
        self.allowed = set(self.config.get("tools", {}).get("enabled") or [])
        self.rate_limiter = RateLimiter(self.config.get("security", {}).get("rate_limit_per_minute", 30))
        self.auditor = AuditLogger()
        self.role = default_role
        self._llm = llm_client
        if self._llm is None and self.config.get("llm", {}).get("enabled", True):
            try:
                from research.llm import LLMClient
                self._llm = LLMClient()
            except Exception:  # noqa: BLE001
                self._llm = None

    def _suggestions(self, ctx: CopilotContext) -> list[str]:
        if not ctx.positions:
            return ["What is my portfolio summary?", "Show my asset allocation."]
        top = max(ctx.positions, key=lambda p: float(p.get("value") or 0))
        name = top.get("name") or top.get("security_id")
        return [
            "What is my portfolio XIRR?",
            "Which holdings drive concentration?",
            "Show my allocation vs targets.",
            f"What happens if I trim 25% of {name}?",
            "How can I rebalance with ₹5 lakh of new investments?",
            f"Explain the signal for {name}.",
            f"Research {name}.",
        ]

    def _clarify(self, question: str, missing: str, ctx: CopilotContext, intent: Intent) -> CopilotResponse:
        names = list(ctx.names.values())[:12]
        msg = (f"I need a security to answer that. Available holdings include: {', '.join(names) or 'none imported'}. "
               f"Please specify which holding you mean.")
        result = ToolResult(intent.tool, False, msg, {"missing": missing}, [],
                            [f"Missing '{missing}' for {intent.tool}."])
        return CopilotResponse(build_answer(result), [result], intent=intent.tool,
                               suggestions=self._suggestions(ctx))

    # -- main entry -----------------------------------------------------
    def answer(self, question: str, app_ctx: AppContext | None = None, *,
               copilot_ctx: CopilotContext | None = None, session_id: str = "default") -> CopilotResponse:
        cfg = self.config
        max_chars = int(cfg.get("security", {}).get("max_input_chars", 2000))
        clean = sanitise_user_input(question, max_chars=max_chars)
        if not clean:
            empty = ToolResult("clarify", False, "Please ask a question about your portfolio.",
                               {}, [], ["Empty question."])
            return CopilotResponse(build_answer(empty), [empty], intent=None, suggestions=[])

        if not self.rate_limiter.allow(session_id):
            limited = ToolResult("rate_limited", False,
                                 "Too many requests — please wait a moment before asking again.",
                                 {}, [], ["Rate limit exceeded."])
            return CopilotResponse(build_answer(limited), [limited], intent=None)

        if copilot_ctx is not None:
            ctx = copilot_ctx
        elif app_ctx is not None:
            ctx = build_copilot_context(app_ctx)
        else:
            err = ToolResult("context", False, "Portfolio context is unavailable.",
                             {}, [], ["No AppContext supplied."])
            return CopilotResponse(build_answer(err), [err], intent=None)

        # ---- intent ----
        intent = classify(clean, ctx)
        if (intent.confidence < 0.6 and cfg.get("llm", {}).get("intent_fallback", True)
                and self._llm is not None and self.allowed):
            llm_intent = classify_with_llm(clean, sorted(self.allowed), self._llm)
            if llm_intent is not None:
                intent = llm_intent

        tool_name = _resolve_tool_name(intent, self.allowed)
        spec = get_tool(tool_name)
        if spec is None or not authorize(self.role, tool_name):
            err = ToolResult(tool_name or "none", False, "That capability is not available or not authorized.",
                             {}, [], [f"Tool '{tool_name}' unavailable/unauthorized."])
            return CopilotResponse(build_answer(err), [err], intent=tool_name,
                                   suggestions=self._suggestions(ctx))

        # ---- validate args ----
        validation = validate_args(spec, intent.args)
        if not validation.ok:
            if "missing required" in (validation.error or ""):
                return self._clarify(clean, "security", ctx, intent)
            result = ToolResult(tool_name, False, f"Invalid input: {validation.error}", {}, [],
                                [validation.error or "invalid input"])
            return CopilotResponse(build_answer(result), [result], intent=tool_name,
                                   suggestions=self._suggestions(ctx))

        # ---- execute ----
        try:
            result = spec.run(validation.args, ctx)
        except Exception as exc:  # noqa: BLE001 — never crash the chat
            result = ToolResult(tool_name, False, f"The {tool_name} tool could not complete.",
                                {"error": str(exc)}, [], [f"{tool_name} failed: {exc}"])
        self.auditor.record(tool_name, validation.args, result.ok, result.summary, session_id)

        # ---- narrate (facts fixed) ----
        narrative = None
        if result.ok and self._llm is not None and cfg.get("llm", {}).get("narrate", True):
            narrative = narrate(result, clean, self._llm, int(cfg.get("llm", {}).get("max_tokens", 400)))

        answer_md = build_answer(result, narrative=narrative)
        return CopilotResponse(
            answer_markdown=answer_md,
            tool_results=[result],
            used_llm=bool(narrative),
            provider=self._provider_name(),
            intent=tool_name,
            suggestions=self._suggestions(ctx),
        )

    def _provider_name(self) -> str | None:
        if self._llm is None:
            return None
        try:
            s = self._llm.settings
            if getattr(s, "gemini_api_key", None):
                return "gemini"
            if getattr(s, "openrouter_api_key", None):
                return "openrouter"
        except Exception:  # noqa: BLE001
            return None
        return None


def answer(question: str, app_ctx: AppContext | None = None, *, config: dict | None = None,
           llm_client=None, copilot_ctx: CopilotContext | None = None,
           session_id: str = "default") -> CopilotResponse:
    """Convenience wrapper around :class:`CopilotEngine`."""
    engine = CopilotEngine(config=config, llm_client=llm_client)
    return engine.answer(question, app_ctx, copilot_ctx=copilot_ctx, session_id=session_id)

