"""Alert triggers: translate Stage 8/9 results and data-health into Alerts.

Pure functions — they read already-computed results (recommendations, allocation
report, data-quality summary, freshness) and never fetch data or invent values.
"""
from __future__ import annotations

from datetime import UTC, datetime

from alerts.dedupe import parse_ts
from alerts.models import (
    A_CONCENTRATION,
    A_EARNINGS,
    A_GOVERNANCE,
    A_IMPORT_FAILURE,
    A_RECONCILIATION,
    A_SIGNAL_EXIT,
    A_SIGNAL_MONITOR,
    A_SIGNAL_TRIM,
    A_STALE_DATA,
    A_VALUATION,
    SEV_CRITICAL,
    SEV_INFO,
    SEV_WARN,
    Alert,
)

_SIGNAL_MAP = {
    "CONCENTRATION": A_CONCENTRATION,
    "VALUATION": A_VALUATION,
    "GOVERNANCE": A_GOVERNANCE,
    "FUNDAMENTAL_DETERIORATION": A_EARNINGS,
}


def _rec_alerts(rec: dict) -> list[Alert]:
    sid = str(rec.get("security_id") or "").strip()
    action = rec.get("action") or ""
    out: list[Alert] = []
    if sid and action in ("EXIT_REVIEW", "REPLACEMENT_REVIEW"):
        out.append(Alert(A_SIGNAL_EXIT, SEV_CRITICAL, f"{sid}: {action}",
                         rec.get("reasoning") or f"{action} — review required.",
                         security_id=sid, dedupe_key=action, evidence={"confidence": rec.get("confidence")}))
    elif sid and action == "TRIM_REVIEW":
        out.append(Alert(A_SIGNAL_TRIM, SEV_WARN, f"{sid}: TRIM_REVIEW",
                         rec.get("reasoning") or "Position flagged for trim review.",
                         security_id=sid, dedupe_key=action, evidence={"confidence": rec.get("confidence")}))
    elif sid and action == "MONITOR":
        out.append(Alert(A_SIGNAL_MONITOR, SEV_INFO, f"{sid}: MONITOR",
                         rec.get("reasoning") or "Position under monitoring.",
                         security_id=sid, dedupe_key=action))

    for sig in rec.get("signals") or []:
        stype = sig.get("signal_type")
        atype = _SIGNAL_MAP.get(stype)
        if not atype:
            continue
        sev = sig.get("severity") or SEV_WARN
        out.append(Alert(atype, sev, f"{sid}: {stype}",
                         sig.get("message") or stype,
                         security_id=sid or None, dedupe_key=stype,
                         evidence={"current_value": sig.get("current_value"),
                                   "threshold_value": sig.get("threshold_value")}))
    return out


def build_alerts(app_ctx=None, decision_recs=None, preferences=None, now=None,
                 allocation_report=None, data_quality=None, data_freshness=None) -> list[Alert]:
    """Aggregate all alert triggers into a de-duplicatable list of Alerts."""
    now = now or datetime.now(UTC)
    alerts: list[Alert] = []

    recs = decision_recs or {}
    for rec in (recs.get("equity") or []) + (recs.get("mf") or []):
        alerts.extend(_rec_alerts(rec))

    if allocation_report:
        for cls in allocation_report.get("breached") or []:
            row = next((r for r in allocation_report.get("rows", []) if r.get("asset_class") == cls), {})
            alerts.append(Alert(
                A_CONCENTRATION, SEV_WARN, f"Allocation breach: {cls}",
                f"{cls} at {row.get('current_pct')}% vs target {row.get('target_pct')}% "
                f"(allowed {row.get('min_pct')}–{row.get('max_pct')}%).",
                security_id=None, dedupe_key=f"breach:{cls}",
                evidence={"current_pct": row.get("current_pct"), "target_pct": row.get("target_pct")},
            ))

    dq = data_quality or {}
    if dq.get("unresolved_identifiers"):
        alerts.append(Alert(A_IMPORT_FAILURE, SEV_WARN, "Unresolved security identifiers",
                            f"{len(dq['unresolved_identifiers'])} identifier(s) could not be resolved.",
                            dedupe_key="unresolved_ids",
                            evidence={"count": len(dq["unresolved_identifiers"])}))
    if (dq.get("duplicate_or_conflicting_records") or 0) > 0:
        alerts.append(Alert(A_IMPORT_FAILURE, SEV_INFO, "Duplicate/conflicting records",
                            f"{dq['duplicate_or_conflicting_records']} duplicate or conflicting record(s) detected.",
                            dedupe_key="duplicate_records",
                            evidence={"count": dq["duplicate_or_conflicting_records"]}))
    if (dq.get("holdings_reconciliation_differences") or 0) > 0:
        alerts.append(Alert(A_RECONCILIATION, SEV_WARN, "Holdings reconciliation differences",
                            f"{dq['holdings_reconciliation_differences']} difference(s) vs reported holdings.",
                            dedupe_key="recon_diff",
                            evidence={"count": dq["holdings_reconciliation_differences"]}))
    if (dq.get("missing_price_anomalies") or 0) > 0:
        alerts.append(Alert(A_STALE_DATA, SEV_WARN, "Missing price data",
                            f"{dq['missing_price_anomalies']} price anomaly/anomalies detected.",
                            dedupe_key="price_anomalies",
                            evidence={"count": dq["missing_price_anomalies"]}))

    df = data_freshness or {}
    if df.get("as_of"):
        asof = parse_ts(df["as_of"])
        max_age = float(df.get("max_age_days", 3))
        if asof is not None:
            age_days = (now - asof).total_seconds() / 86400.0
            if age_days > max_age:
                alerts.append(Alert(A_STALE_DATA, SEV_WARN,
                                    f"Stale data: {df.get('label', 'market data')}",
                                    f"Latest {df.get('label', 'data')} is {age_days:.1f} days old "
                                    f"(threshold {max_age:.0f}d).",
                                    dedupe_key=f"stale:{df.get('label', 'data')}",
                                    evidence={"age_days": round(age_days, 1)}))
    return alerts
