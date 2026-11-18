"""UI helpers for the Stage 8 decision engine (shadow mode).

Builds recommendations from available portfolio + MF data (never inventing missing
research), caches them per session, and renders explainable summaries for the
Portfolio / Holdings & Research / Performance pages. No trades are executed.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.context import AppContext
from decision.config import load_thresholds
from decision.engine import ACTION_HOLD, DecisionEngine


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


def _equity_recommendations(ctx: AppContext, engine: DecisionEngine) -> list[dict]:
    holdings = ctx.holdings
    if holdings is None or getattr(holdings, "empty", True):
        return []
    df = holdings.copy()
    total = 0.0
    for _, r in df.iterrows():
        try:
            total += float(r.get("current_value") or 0)
        except (TypeError, ValueError):
            continue
    recs: list[dict] = []
    for _, r in df.iterrows():
        isin = str(r.get("isin") or "").strip().upper()
        if not isin:
            continue
        try:
            cv = float(r.get("current_value") or 0)
        except (TypeError, ValueError):
            cv = 0.0
        weight = (cv / total * 100) if total > 0 else 0.0
        holding = {
            "security_id": isin,
            "weight_pct": weight,
            "portfolio_value": total,
            "price_gain_6m": _price_gain_6m(ctx.market_db_path, isin),
        }
        rec = engine.evaluate_equity(holding, research={}, base_case_cagr=None)
        rec["security_name"] = str(r.get("symbol") or isin)
        recs.append(rec)
    return recs


def _mf_recommendations(ctx: AppContext, engine: DecisionEngine) -> list[dict]:
    try:
        from app.mf_ui import get_mf_context
        mf = get_mf_context(ctx)
    except Exception:  # noqa: BLE001
        return []
    if not mf.get("has_mf_data"):
        return []
    overlap_max: dict[str, float] = {}
    for p in mf.get("overlap", {}).get("pairs", []):
        overlap_max[p["scheme_a"]] = max(overlap_max.get(p["scheme_a"], 0.0), p.get("weight_overlap_pct", 0.0))
        overlap_max[p["scheme_b"]] = max(overlap_max.get(p["scheme_b"], 0.0), p.get("weight_overlap_pct", 0.0))
    # best overlap pair per scheme (for jaccard)
    jac_max: dict[str, float] = {}
    for p in mf.get("overlap", {}).get("pairs", []):
        jac_max[p["scheme_a"]] = max(jac_max.get(p["scheme_a"], 0.0), p.get("jaccard", 0.0))
        jac_max[p["scheme_b"]] = max(jac_max.get(p["scheme_b"], 0.0), p.get("jaccard", 0.0))
    recs: list[dict] = []
    for row in mf.get("scheme_rows", []):
        code = row.get("scheme_code")
        metrics = {
            "scheme_code": code,
            "scheme_name": row.get("scheme_name"),
            "analytics": row.get("analytics", {}),
            "expense_ratio": row.get("expense_ratio"),
            "manager_changes": row.get("manager_changes"),
        }
        om = {"weight_overlap_pct": overlap_max.get(code), "jaccard": jac_max.get(code)}
        rec = engine.evaluate_mf(metrics, om)
        rec["security_name"] = row.get("scheme_name") or code
        recs.append(rec)
    return recs


def compute_recommendations(ctx: AppContext) -> dict:
    """Build (and session-cache) shadow-mode recommendations for equity + MF."""
    config = load_thresholds()
    engine = DecisionEngine(config)
    equity = _equity_recommendations(ctx, engine)
    mf = _mf_recommendations(ctx, engine)
    return {"equity": equity, "mf": mf, "config": config}


def attention_items(recs: dict) -> list[dict]:
    """Flatten recommendations that need attention (non-HOLD), most urgent first."""
    order = {"REPLACEMENT_REVIEW": 0, "EXIT_REVIEW": 1, "TRIM_REVIEW": 2, "MONITOR": 3, "HOLD": 4}
    items = [r for r in (recs.get("equity", []) + recs.get("mf", [])) if r.get("action") != ACTION_HOLD]
    return sorted(items, key=lambda r: (order.get(r.get("action"), 9), -r.get("confidence", 0)))


def recommendations_frame(recs: list[dict]) -> pd.DataFrame:
    rows = []
    for r in recs:
        rows.append({
            "security": r.get("security_name") or r.get("security_id"),
            "type": r.get("security_type"),
            "action": r.get("action"),
            "confidence": r.get("confidence"),
            "critical": r.get("signal_counts", {}).get("critical", 0),
            "warnings": r.get("signal_counts", {}).get("warn", 0),
        })
    return pd.DataFrame(rows)


def render_recommendation_detail(rec: dict, security_id: str, portfolio_id=None) -> None:
    """Render an explainable recommendation with acknowledge / record-decision controls."""
    action = rec.get("action")
    conf = rec.get("confidence")
    st.metric("Decision Recommendation", action, f"confidence {conf:.2f}" if isinstance(conf, (int, float)) else None)

    sigs = rec.get("signals") or []
    if sigs:
        sig_df = pd.DataFrame([
            {
                "type": s.get("signal_type"),
                "severity": s.get("severity"),
                "tier": s.get("tier"),
                "message": s.get("message"),
            }
            for s in sigs
        ])
        ui_safe(sig_df)
    else:
        st.caption("No material review signals.")

    tiers = rec.get("tiers") or {}
    if any(tiers.values()):
        with st.expander("Facts vs Forecasts vs AI judgments", expanded=False):
            if tiers.get("facts"):
                st.markdown("**Facts (MCP disclosures):**")
                for t in tiers["facts"]:
                    st.write(f"- {t}")
            if tiers.get("analyst_consensus"):
                st.markdown("**Analyst consensus / estimates:**")
                for t in tiers["analyst_consensus"]:
                    st.write(f"- {t}")
            if tiers.get("ai_forecasts"):
                st.markdown("**AI forecasts (not consensus):**")
                for t in tiers["ai_forecasts"]:
                    st.write(f"- {t}")

    if rec.get("reasoning"):
        st.write("**Reasoning:** " + rec["reasoning"])
    if rec.get("llm_opinion") and rec["llm_opinion"].get("text"):
        st.info(f"AI interpretation ({rec['llm_opinion'].get('provider')}): {rec['llm_opinion']['text']}")
    if rec.get("tax"):
        tax = rec["tax"]
        st.caption(f"Tax estimate: {tax.get('note', '')} (estimated tax Rs {tax.get('estimated_tax')})")
    if rec.get("evidence_ids"):
        st.caption("Evidence: " + ", ".join(str(e) for e in rec["evidence_ids"]))

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Acknowledge & save to history", key=f"ack_{security_id}"):
            try:
                from decision.storage import acknowledge_recommendation, save_recommendation
                rid = save_recommendation(rec, portfolio_id=portfolio_id)
                acknowledge_recommendation(rid)
                st.success(f"Acknowledged & saved (recommendation #{rid}).")
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not save: {e}")
    with c2:
        decision = st.selectbox("Record my decision", ["HELD", "TRIMMED", "SOLD", "IGNORED"], key=f"dec_{security_id}")
        if st.button("Record decision", key=f"recd_{security_id}"):
            try:
                from decision.storage import record_decision, save_recommendation
                rid = save_recommendation(rec, portfolio_id=portfolio_id)
                record_decision(rid, security_id, decision)
                st.success(f"Decision '{decision}' recorded.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not record: {e}")
    st.caption("Shadow mode — advisory only; no trades are executed.")


def ui_safe(df) -> None:
    try:
        st.dataframe(df, hide_index=True, width="stretch")
    except Exception:  # noqa: BLE001
        st.write(df)

