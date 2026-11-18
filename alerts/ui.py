"""In-app alert center (rendered inside the Portfolio page — no new nav page)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from alerts.preferences import load_preferences
from alerts.store import AlertStore


def _scan(ctx) -> list:
    """Build alerts from already-computed results (no network, no data invention)."""
    from alerts.triggers import build_alerts
    recs = st.session_state.get("decision_recs")
    alloc = None
    try:
        from portfolio.ui import get_rebalance_context
        alloc = get_rebalance_context(ctx)["report"]
    except Exception:  # noqa: BLE001
        alloc = None
    dq = None
    try:
        from reports.exports import get_data_quality_summary
        dq = get_data_quality_summary(ctx.txs, ctx.holdings)
    except Exception:  # noqa: BLE001
        dq = None
    return build_alerts(decision_recs=recs, allocation_report=alloc, data_quality=dq)


def render_alert_center(ctx, key_prefix: str = "alerts") -> None:
    """Render the alert center: severity summary, table and acknowledge controls."""
    try:
        store = AlertStore()
        prefs = load_preferences()
    except Exception as e:  # noqa: BLE001
        st.caption(f"Alerts unavailable: {e}")
        return

    st.markdown("#### Intelligent Alerts")
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Scan for new alerts", key=f"{key_prefix}_scan"):
            try:
                generated = _scan(ctx)
                kept = store.record_many(generated, preferences=prefs)
                st.success(f"Evaluated {len(generated)} alert(s); {len(kept)} new after de-duplication.")
            except Exception as e:  # noqa: BLE001
                st.warning(f"Alert scan failed: {e}")
    with c2:
        st.caption("Alerts are advisory & de-duplicated. External channels (email/webhook) are stubs — "
                   "nothing is sent automatically.")

    alerts = store.list_alerts(limit=100)
    if not alerts:
        st.caption("No alerts yet. Click 'Scan for new alerts' or schedule the evaluate_alerts job.")
        return

    counts = store.counts_by_severity()
    m1, m2, m3 = st.columns(3)
    m1.metric("Critical", counts.get("CRITICAL", 0))
    m2.metric("Warning", counts.get("WARN", 0))
    m3.metric("Info", counts.get("INFO", 0))

    sev = st.selectbox("Filter by severity", ["All", "CRITICAL", "WARN", "INFO"], key=f"{key_prefix}_sev")
    filtered = [a for a in alerts if sev == "All" or a["severity"] == sev]
    df = pd.DataFrame([
        {"created_at": a["created_at"], "severity": a["severity"], "type": a["alert_type"],
         "security": a["security_id"], "message": a["message"],
         "acknowledged": a["acknowledged"], "suppressed": a["suppressed_count"]}
        for a in filtered
    ])
    if df.empty:
        st.caption("No alerts match the selected filter.")
        return
    st.dataframe(df, hide_index=True, width="stretch")

    unack = [a for a in filtered if not a["acknowledged"]]
    if unack:
        labels = {f"#{a['id']} · {a['alert_type']} · {a['security_id'] or 'portfolio'}": a["id"] for a in unack}
        choice = st.selectbox("Acknowledge an alert", list(labels), key=f"{key_prefix}_ack_sel")
        if st.button("Acknowledge", key=f"{key_prefix}_ack_btn") and store.acknowledge(labels[choice]):
            st.rerun()
