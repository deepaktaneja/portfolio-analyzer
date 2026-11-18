"""Response building: turn deterministic ToolResults into a cited, tiered answer.

The LLM (when available) is used ONLY to phrase a short explanation around the
figures supplied by the tool — it is explicitly forbidden from computing numbers.
"""
from __future__ import annotations

import json

from copilot.types import TIER_AI, TIER_ESTIMATE, TIER_FACT, ToolResult

_TIER_BADGE = {
    TIER_FACT: "🟢 Verified (deterministic engine)",
    TIER_ESTIMATE: "🟡 Estimated / provisional",
    TIER_AI: "🟣 AI interpretation (not a computed fact)",
}


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return str(v)


def _bullets(data: dict, limit: int = 8) -> list[str]:
    out: list[str] = []
    if not isinstance(data, dict):
        return out
    for k, v in data.items():
        if k in ("actions", "holdings", "rows", "recommendations", "signals", "by_holding",
                 "events", "pairs", "top_contributors", "report_md", "drift_after"):
            continue
        if isinstance(v, (int, float, str, bool)) or v is None:
            out.append(f"- **{k}**: {_fmt(v)}")
        if len(out) >= limit:
            break
    return out


def _table_rows(data: dict, key: str, cols: list[str], limit: int = 8) -> list[str]:
    rows = data.get(key) if isinstance(data, dict) else None
    if not rows:
        return []
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows[:limit]:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return out


def build_answer(result: ToolResult, narrative: str | None = None) -> str:
    """Compose the markdown answer for a single tool result."""
    lines: list[str] = [f"**{result.summary}**"]
    if narrative:
        lines += ["", narrative]

    bullets = _bullets(result.data)
    if bullets:
        lines += ["", "**Key figures**"] + bullets

    # Structured tables for common result shapes.
    if isinstance(result.data, dict):
        hold_cols = ["name", "value", "weight_pct"]
        if result.tool == "holdings" and not any("name" in r for r in (result.data.get("holdings") or [])):
            hold_cols = ["security_id", "value", "weight_pct"]
        tbl = _table_rows(result.data, "holdings", hold_cols)
        if tbl:
            lines += ["", "**Holdings**"] + tbl
        recs = _table_rows(result.data, "recommendations", ["name", "action", "confidence"])
        if recs:
            lines += ["", "**Signals**"] + recs
        acts = _table_rows(result.data, "actions", ["action", "security_id", "asset_class", "amount", "estimated_tax"])
        if acts:
            lines += ["", "**Proposed actions**"] + acts

    if result.data.get("report_md"):
        lines += ["", "**Research report**", "", str(result.data["report_md"])[:4000]]

    if result.warnings:
        lines += ["", "**⚠️ Missing data / assumptions**"] + [f"- {w}" for w in result.warnings]

    if result.citations:
        lines += ["", "**Sources**"] + [f"- {c.label()}" for c in result.citations]

    lines += ["", f"_{_TIER_BADGE.get(result.tier, result.tier)}_", "",
              "_Advisory only — no trades are executed._"]
    return "\n".join(lines)


def narrate(result: ToolResult, question: str, llm_client, max_tokens: int = 400) -> str | None:
    """Ask the LLM to phrase an explanation around the tool's facts (never compute)."""
    if llm_client is None:
        return None
    facts = json.dumps({"summary": result.summary, "data": _shrink(result.data),
                        "warnings": result.warnings}, default=str)[:2500]
    system = (
        "You are an investment-copilot explainer. Use ONLY the numbers in FACTS. "
        "Do not compute, extrapolate or invent any figure. If a value is missing, say so. "
        "Answer the user's question in 2-4 concise sentences, citing that figures come from the portfolio engine."
    )
    user = f"QUESTION: {question}\n\nFACTS (authoritative, do not alter):\n{facts}"
    try:
        res = llm_client.generate(system, user)
    except Exception:  # noqa: BLE001
        return None
    if not res or res.provider == "none" or not res.text.strip():
        return None
    return res.text.strip()[:max_tokens * 4]


def _shrink(data) -> dict:
    """Keep only scalar/short values for the narration prompt."""
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if isinstance(v, (int, float, str, bool)) or v is None or isinstance(v, list) and len(v) <= 5 and all(isinstance(x, dict) for x in v):
            out[k] = v
    return out
