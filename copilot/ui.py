"""Streamlit chat panel for the AI Copilot (rendered inside existing pages)."""
from __future__ import annotations

import uuid

import streamlit as st

from app.context import AppContext

_MAX_TURNS = 20


def _get_engine():
    if "copilot_engine" not in st.session_state:
        from copilot.engine import CopilotEngine
        st.session_state["copilot_engine"] = CopilotEngine()
    return st.session_state["copilot_engine"]


def _session_id() -> str:
    if "copilot_session_id" not in st.session_state:
        st.session_state["copilot_session_id"] = uuid.uuid4().hex
    return st.session_state["copilot_session_id"]


def _history() -> list[dict]:
    return st.session_state.setdefault("copilot_history", [])


def _append(role: str, content: str, meta: dict | None = None, page: str | None = None) -> None:
    hist = _history()
    hist.append({"role": role, "content": content, "meta": meta or {}, "page": page})
    if len(hist) > _MAX_TURNS * 2:
        del hist[: len(hist) - _MAX_TURNS * 2]


def _process(app_ctx: AppContext, question: str, page_key: str) -> None:
    _append("user", question, page=page_key)
    try:
        engine = _get_engine()
        resp = engine.answer(question, app_ctx, session_id=_session_id())
    except Exception as exc:  # noqa: BLE001 — never break the page
        _append("assistant", f"Sorry — the copilot hit an error: {exc}", {"error": str(exc)}, page_key)
        return
    _append("assistant", resp.answer_markdown, resp.to_dict(), page_key)


def _render_message(msg: dict) -> None:
    role = msg.get("role", "assistant")
    with st.chat_message("user" if role == "user" else "assistant"):
        st.markdown(msg.get("content", ""))
        meta = msg.get("meta") or {}
        results = meta.get("tool_results") or []
        if results:
            with st.expander("🔎 Supporting calculations & sources", expanded=False):
                for r in results:
                    st.markdown(f"**Tool:** `{r.get('tool')}` · tier: `{r.get('tier')}`")
                    cites = r.get("citations") or []
                    if cites:
                        st.markdown("**Sources**")
                        for c in cites:
                            ts = f" @ {c.get('timestamp')}" if c.get("timestamp") else ""
                            st.caption(f"🔗 {c.get('source')}: {c.get('detail')}{ts}")
                    if r.get("warnings"):
                        st.markdown("**Warnings / missing data**")
                        for w in r["warnings"]:
                            st.warning(w)
                    if r.get("data"):
                        st.json(r["data"])


_STATIC_SUGGESTIONS = {
    "portfolio": ["What is my portfolio value and XIRR?", "Which holdings drive concentration?",
                  "Show my asset allocation.", "How can I rebalance with ₹5 lakh?"],
    "performance": ["What is my portfolio XIRR?", "Show my unrealized gains.",
                    "Show my asset allocation.", "Which holdings drive concentration?"],
    "holdings": ["Show my top holdings.", "Show my signals.", "Show my unrealized gains.",
                 "What is my portfolio value and XIRR?"],
    "backtesting": ["Show my signals.", "What is my portfolio XIRR?", "Show my top holdings.",
                    "Which holdings drive concentration?"],
}


def _render_suggestions(app_ctx: AppContext, page_key: str) -> str | None:
    """Static, context-free suggestions (keeps page render fast; no data loading)."""
    suggestions = _STATIC_SUGGESTIONS.get(page_key, _STATIC_SUGGESTIONS["portfolio"])
    cols = st.columns(2)
    for i, s in enumerate(suggestions):
        if cols[i % 2].button(s, key=f"copilot_sug_{page_key}_{i}"):
            return s
    return None


def render_copilot(app_ctx: AppContext, page_key: str = "portfolio", expanded: bool = False) -> None:
    """Render the 'Ask My Portfolio' conversational panel (advisory only)."""
    with st.expander("🤖 Ask My Portfolio (AI Copilot)", expanded=expanded):
        st.caption("Ask questions in plain English. Figures come from the deterministic portfolio engines; "
                   "the AI only explains them. Advisory only — no trades. Conversation stays in this session.")
        try:
            pending = st.session_state.pop("copilot_pending", None)
            sugg = _render_suggestions(app_ctx, page_key) if not pending else None

            with st.form(key=f"copilot_form_{page_key}", clear_on_submit=True):
                question = st.text_input("Your question", placeholder="e.g. What is my XIRR? / Trim 25% of my largest holding?")
                submitted = st.form_submit_button("Ask")

            if pending:
                _process(app_ctx, pending, page_key)
            elif submitted and question.strip():
                _process(app_ctx, question.strip(), page_key)
            elif sugg:
                _process(app_ctx, sugg, page_key)

            hist = _history()
            if hist:
                for msg in hist[-_MAX_TURNS * 2:]:
                    _render_message(msg)
                cols = st.columns([1, 3])
                if cols[0].button("Clear conversation", key=f"copilot_clear_{page_key}"):
                    st.session_state["copilot_history"] = []
                    st.rerun()
                cols[1].caption("🔒 Session-only history; not persisted. Portfolio identifiers are redacted in logs.")
            else:
                st.caption("No questions yet — try a suggestion above.")
        except Exception as exc:  # noqa: BLE001 — the panel must never break a page
            st.info(f"Copilot panel unavailable: {exc}")
