"""Intent routing: map a natural-language question to a deterministic tool call.

Deterministic keyword/argument parsing is the primary path (works fully offline).
When configured and available, the LLM may propose a structured {tool,args} JSON
*only* for tools in the allowlist; the engine validates arguments before execution.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from copilot.context import CopilotContext

_STOPWORDS = {"the", "a", "an", "my", "of", "in", "on", "for", "to", "and", "with", "what",
              "is", "are", "how", "much", "does", "do", "should", "i", "if", "about", "from"}


@dataclass
class Intent:
    tool: str
    args: dict = field(default_factory=dict)
    confidence: float = 0.6
    reason: str = ""


def parse_amount(text: str) -> float | None:
    """Parse an INR amount like '5 lakh', '₹5,00,000', '2.5 crore', '50k'."""
    t = str(text).lower().replace(",", "")
    best = None
    for m in re.finditer(r"(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(lakh|lakhs|lac|crore|cr|k|thousand)?", t):
        val = float(m.group(1))
        unit = (m.group(2) or "").strip()
        mult = {"lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "crore": 1e7, "cr": 1e7,
                "k": 1e3, "thousand": 1e3}.get(unit, 1.0)
        amount = val * mult
        if best is None or amount > best:
            best = amount
    return best


def extract_percent(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(text))
    return float(m.group(1)) if m else None


def _mentioned_securities(question: str, ctx: CopilotContext) -> list[str]:
    q = question.upper()
    hits: list[tuple[int, str]] = []
    for sid in ctx.security_ids:
        name = str(ctx.names.get(sid, "")).upper()
        idx = q.find(sid)
        if idx >= 0:
            hits.append((idx, sid))
            continue
        for tok in re.findall(r"[A-Z0-9]{3,}", name):
            if tok in q and tok.lower() not in _STOPWORDS:
                hits.append((q.find(tok), sid))
                break
    seen: set[str] = set()
    ordered = []
    for _, sid in sorted(hits):
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    return ordered


def _fallback_symbol(question: str) -> str | None:
    for tok in re.findall(r"\b[A-Z][A-Z0-9&.\-]{1,19}\b", question):
        if tok.upper() not in _STOPWORDS:
            return tok
    return None


def classify(question: str, ctx: CopilotContext) -> Intent:
    """Deterministic intent classification with argument extraction."""
    q = question.lower()
    secs = _mentioned_securities(question, ctx)
    first = secs[0] if secs else None

    if "replace" in q or "instead of" in q or ("swap" in q and len(secs) >= 2):
        if len(secs) >= 2:
            return Intent("replacement_simulation", {"security_a": secs[0], "security_b": secs[1]}, 0.9,
                          "replace A with B")
        return Intent("replacement_simulation", {"security_a": first, "security_b": None}, 0.4,
                      "replace needs two securities")

    if ("why" in q or "explain" in q or "reason" in q) and any(
            k in q for k in ("trim", "exit", "hold", "signal", "review", "sell", "recommend")):
        return Intent("explain_signal", {"security": first}, 0.85 if first else 0.5, "explain signal")

    if "trim" in q or ("sell" in q and extract_percent(question)):
        pct = extract_percent(question)
        return Intent("trim_simulation",
                      {"security": first, "pct": pct if pct is not None else 25.0},
                      0.9 if (first and pct) else 0.5, "trim scenario")

    if "rebalance" in q or "new investment" in q or ("invest" in q and parse_amount(question)):
        return Intent("rebalance_plan", {"cash": parse_amount(question) or 0.0}, 0.85, "rebalance")

    if "overlap" in q or "duplicate" in q:
        return Intent("overlap", {}, 0.85, "fund overlap")

    if any(k in q for k in ("research", "valuation", "earnings", "governance", "analyst", "outlook", "fundamental")):
        sym = first or _fallback_symbol(question)
        return Intent("research_report", {"security": sym}, 0.8 if sym else 0.4, "research")

    if any(k in q for k in ("portfolio value", "net worth", "summary", "overview", "how am i doing")):
        return Intent("portfolio_summary", {}, 0.85, "summary")

    if "xirr" in q or "return" in q or "cagr" in q or "performance" in q:
        return Intent("xirr", {"security": first}, 0.85, "xirr")

    if "allocation" in q or "asset class" in q or "drift" in q:
        return Intent("allocation", {}, 0.85, "allocation")

    if "concentration" in q or "contribut" in q or "diversif" in q or "risk" in q:
        return Intent("concentration", {}, 0.85, "concentration")

    if "gain" in q or "profit" in q or "p&l" in q or "unrealized" in q:
        return Intent("gains", {}, 0.85, "gains")

    if "dividend" in q or "idcw" in q:
        return Intent("dividends", {"security": first}, 0.85, "dividends")

    if "signal" in q or "should i" in q or "review" in q:
        return Intent("signals", {}, 0.7, "signals")

    if "holding" in q or "largest" in q or "top position" in q:
        return Intent("holdings", {}, 0.8, "holdings")

    return Intent("portfolio_summary", {}, 0.4, "default summary")


def classify_with_llm(question: str, tool_names: list[str], llm_client) -> Intent | None:
    """Ask the LLM for a structured {tool,args} intent (validated by the caller)."""
    if llm_client is None:
        return None
    system = (
        "You are an intent router for a portfolio assistant. Return ONLY compact JSON "
        '{"tool": "<name>", "args": {...}}. Choose a tool from this list: ' + ", ".join(tool_names) +
        ". Never compute numbers. If unsure, use 'portfolio_summary'."
    )
    try:
        result = llm_client.generate(system, question)
    except Exception:  # noqa: BLE001
        return None
    if not result or result.provider == "none" or not result.text.strip():
        return None
    try:
        text = result.text.strip()
        start, end = text.find("{"), text.rfind("}")
        payload = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    tool = payload.get("tool")
    if tool not in tool_names:
        return None
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    return Intent(tool, args, 0.7, "llm-routed")

