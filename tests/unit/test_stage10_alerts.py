"""Unit tests for Stage 10 alerts (triggers, dedupe, preferences)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alerts.models import (
    A_CONCENTRATION,
    A_GOVERNANCE,
    A_IMPORT_FAILURE,
    A_RECONCILIATION,
    A_SIGNAL_EXIT,
    A_SIGNAL_MONITOR,
    A_STALE_DATA,
    SEV_CRITICAL,
    SEV_INFO,
    Alert,
)
from alerts.preferences import resolve
from alerts.store import AlertsBase, AlertStore
from alerts.triggers import build_alerts


def make_store(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path/'alerts.db'}", future=True)
    AlertsBase.metadata.create_all(eng)
    return AlertStore(sessionmaker(bind=eng, future=True)())


def test_build_alerts_from_recommendations():
    recs = {"equity": [{
        "security_id": "INE1", "action": "EXIT_REVIEW", "reasoning": "governance issue",
        "signals": [{"signal_type": "GOVERNANCE", "severity": "CRITICAL", "message": "red flag"}],
    }], "mf": []}
    alerts = build_alerts(decision_recs=recs)
    types = {a.alert_type for a in alerts}
    assert A_SIGNAL_EXIT in types
    assert A_GOVERNANCE in types


def test_build_alerts_allocation_and_data_quality():
    alloc = {"breached": ["Equity"], "rows": [
        {"asset_class": "Equity", "current_pct": 90.0, "target_pct": 65.0, "min_pct": 50.0, "max_pct": 80.0}]}
    dq = {"unresolved_identifiers": ["X"], "holdings_reconciliation_differences": 2}
    alerts = build_alerts(allocation_report=alloc, data_quality=dq)
    types = {a.alert_type for a in alerts}
    assert A_CONCENTRATION in types
    assert A_IMPORT_FAILURE in types
    assert A_RECONCILIATION in types


def test_build_alerts_stale_data():
    now = datetime(2026, 5, 1, tzinfo=UTC)
    alerts = build_alerts(
        data_freshness={"label": "prices", "as_of": (now - timedelta(days=10)).isoformat(), "max_age_days": 3},
        now=now,
    )
    assert any(a.alert_type == A_STALE_DATA for a in alerts)


def test_record_dedupes_within_cooldown(tmp_path):
    store = make_store(tmp_path)
    alert = Alert(A_SIGNAL_EXIT, SEV_CRITICAL, "t", "m", security_id="INE1", dedupe_key="EXIT_REVIEW")
    stored1, sup1 = store.record(alert)
    stored2, sup2 = store.record(alert)
    assert stored1 is not None and sup1 is False
    assert stored2 is None and sup2 is True
    assert store.counts_by_severity()["CRITICAL"] == 1


def test_preferences_can_disable_type():
    alert = Alert(A_SIGNAL_MONITOR, SEV_INFO, "t", "m", security_id="X")
    res = resolve({"types": {"SIGNAL_MONITOR": {"enabled": False}}}, alert)
    assert res["enabled"] is False


def test_preferences_min_severity_suppresses(tmp_path):
    store = make_store(tmp_path)
    alert = Alert(A_SIGNAL_MONITOR, SEV_INFO, "t", "m", security_id="Y")
    prefs = {"default": {"min_severity": "WARN", "cooldown_minutes": 60}, "types": {}}
    stored, suppressed = store.record(alert, preferences=prefs)
    assert stored is None and suppressed is True


def test_acknowledge(tmp_path):
    store = make_store(tmp_path)
    stored, _ = store.record(Alert(A_GOVERNANCE, SEV_INFO, "t", "m", security_id="Z"))
    assert store.acknowledge(stored["id"]) is True
    active = store.list_alerts(active_only=True)
    assert all(a["id"] != stored["id"] for a in active)
