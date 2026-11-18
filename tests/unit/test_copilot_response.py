"""Stage 12 — response building (citations, tiers, uncertainty)."""
from __future__ import annotations

from types import SimpleNamespace

from copilot.response import build_answer, narrate
from copilot.types import Citation, ToolResult


def _result(**kw):
    base = {"tool": "xirr", "ok": True, "summary": "Portfolio XIRR is 19.49%.",
            "data": {"xirr": 0.1949},
            "citations": [Citation("performance.xirr", "calculate_portfolio_xirr", "2025-01-01")],
            "warnings": ["No dividend data."]}
    base.update(kw)
    return ToolResult(**base)


def test_build_answer_includes_sources_tier_and_footer():
    md = build_answer(_result())
    assert "Portfolio XIRR is 19.49%" in md
    assert "**Sources**" in md
    assert "performance.xirr" in md
    assert "No dividend data." in md
    assert "Advisory only — no trades" in md
    assert "Verified" in md  # tier badge


def test_build_answer_renders_holdings_table():
    res = ToolResult("holdings", True, "Top holdings", {
        "holdings": [{"name": "RELIANCE", "value": 600000, "weight_pct": 60.0}],
    })
    md = build_answer(res)
    assert "| RELIANCE |" in md


def test_narrate_none_without_llm():
    assert narrate(_result(), "q", None) is None


def test_narrate_uses_llm_text():
    class FakeLLM:
        def generate(self, system, user):
            assert "Do not" in system  # strict instruction sent
            return SimpleNamespace(provider="gemini", text="XIRR is strong, per the engine.")

    out = narrate(_result(), "How am I doing?", FakeLLM())
    assert out and "per the engine" in out


def test_narrate_ignores_unavailable_llm():
    class DeadLLM:
        def generate(self, system, user):
            return SimpleNamespace(provider="none", text="")

    assert narrate(_result(), "q", DeadLLM()) is None
