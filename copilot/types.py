"""Core dataclasses for the Investment Copilot."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# Information tiers (mirrors research/decision tiers).
TIER_FACT = "fact"                 # verified computed/engine fact
TIER_ESTIMATE = "estimate"         # analyst estimate / provisional
TIER_AI = "ai_forecast"            # AI-generated interpretation


@dataclass
class Citation:
    """A traceable source for a fact used in an answer."""

    source: str
    detail: str
    timestamp: str | None = None

    def to_dict(self) -> dict:
        return {"source": self.source, "detail": self.detail, "timestamp": self.timestamp}

    def label(self) -> str:
        ts = f" @ {self.timestamp}" if self.timestamp else ""
        return f"🔗 {self.source}: {self.detail}{ts}"


@dataclass
class ToolResult:
    """Structured output of a deterministic copilot tool."""

    tool: str
    ok: bool = True
    summary: str = ""
    data: dict = field(default_factory=dict)
    citations: list[Citation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tier: str = TIER_FACT

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "summary": self.summary,
            "data": self.data,
            "citations": [c.to_dict() for c in self.citations],
            "warnings": list(self.warnings),
            "tier": self.tier,
        }


@dataclass
class CopilotResponse:
    """Final answer returned to the UI."""

    answer_markdown: str
    tool_results: list[ToolResult] = field(default_factory=list)
    used_llm: bool = False
    provider: str | None = None
    intent: str | None = None
    suggestions: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def warnings(self) -> list[str]:
        out: list[str] = []
        for r in self.tool_results:
            out.extend(r.warnings)
        return out

    def to_dict(self) -> dict:
        return {
            "answer_markdown": self.answer_markdown,
            "tool_results": [r.to_dict() for r in self.tool_results],
            "used_llm": self.used_llm,
            "provider": self.provider,
            "intent": self.intent,
            "suggestions": list(self.suggestions),
            "created_at": self.created_at,
        }
