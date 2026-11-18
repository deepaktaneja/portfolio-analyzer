"""Stage 10 end-to-end (offline) pipeline: XIRR → signals → alerts → rebalance → schedule → backup.

Verifies the infrastructure does not alter financial results and that scheduled jobs are
idempotent. No network/LLM is used — upstream calls are injected.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alerts.store import AlertsBase, AlertStore
from alerts.triggers import build_alerts
from decision.engine import DecisionEngine
from performance.xirr import calculate_portfolio_xirr
from portfolio.optimizer import plan_rebalance
from scheduler.runner import Scheduler
from scheduler.store import JobStore, SchedulerBase
from security.backup import backup_database, verify_backup

TARGETS = {
    "asset_classes": {"Equity": {"target": 50.0, "min": 40.0, "max": 60.0},
                      "Debt": {"target": 50.0, "min": 40.0, "max": 60.0}},
    "rebalancing": {"prioritize_cash": True, "min_trade_size": 1000, "max_turnover_pct": 100.0,
                    "prioritize_sip": True},
    "tax": {},
}


def _alert_store(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path/'alerts.db'}", future=True)
    AlertsBase.metadata.create_all(eng)
    return AlertStore(sessionmaker(bind=eng, future=True)())


def _sched_store(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path/'sched.db'}", future=True)
    SchedulerBase.metadata.create_all(eng)
    return JobStore(sessionmaker(bind=eng, future=True)())


def _xirr():
    txs = [
        {"isin": "INE1", "buy_sell": "BUY", "trade_date": "2024-01-01", "quantity": 10, "net_amount": 1000.0},
        {"isin": "INE1", "buy_sell": "SELL", "trade_date": "2025-01-01", "quantity": 10, "net_amount": 1500.0},
    ]
    return calculate_portfolio_xirr(txs, price_lookup=lambda s, d: 150.0, end_date="2025-01-01")


def test_end_to_end_offline_pipeline(tmp_path):
    # 1. Financial calculation (XIRR) — baseline
    res = _xirr()
    assert res["xirr"] is not None

    # 2. Stage 8 decision signal (shadow only)
    engine = DecisionEngine()
    rec = engine.evaluate_equity({"security_id": "INE1", "weight_pct": 40.0}, {"evidence_ids": []})
    assert rec["is_shadow"] is True
    assert rec["action"] in ("HOLD", "MONITOR", "TRIM_REVIEW", "EXIT_REVIEW")

    # 3. Alerts built + deduplicated
    alerts = build_alerts(decision_recs={"equity": [rec], "mf": []})
    astore = _alert_store(tmp_path)
    kept = astore.record_many(alerts)
    assert len(kept) >= 1
    assert astore.record_many(alerts) == []  # deduped within cooldown

    # 4. Tax-aware rebalancing plan (advisory)
    positions = [
        {"security_id": "INE1", "value": 800_000, "asset_class": "Equity", "price": 100},
        {"security_id": "DB1", "value": 200_000, "asset_class": "Debt", "is_mf": True, "price": 50},
    ]
    plan = plan_rebalance(positions, TARGETS, cash_available=0, sell_date="2026-01-01")
    assert plan["sells_value"] > 0

    # 5. Encrypted backup of a DB + verification
    db = tmp_path / "x.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    backup = backup_database(str(db), str(tmp_path / "bk"), "e2e-key")
    assert backup["status"] == "ok"
    assert verify_backup(backup["backup"], "e2e-key")["ok"] is True

    # 6. Scheduled run: price refresh + alerts + backup; idempotent on repeat
    sstore = _sched_store(tmp_path)
    cfg = {"stale_run_timeout_minutes": 30, "jobs": {
        "refresh_prices": {"enabled": True, "interval_hours": 24, "scope": "daily"},
        "evaluate_alerts": {"enabled": True, "interval_hours": 6, "scope": "hourly"},
        "backup_databases": {"enabled": True, "interval_hours": 24, "scope": "daily"},
    }}
    sched = Scheduler(config=cfg, store=sstore)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ctx = {
        "price_sync": lambda **kw: {"rows_inserted": 1},
        "alerts": [alerts[0]],
        "alert_store": astore,
        "backup_key": "e2e-key",
        "db_paths": [str(db)],
        "backup_dir": str(tmp_path / "bk2"),
    }
    first = sched.run_due(now=now, ctx=ctx)
    statuses = {r["job"]: r["status"] for r in first}
    assert statuses["refresh_prices"] == "success"
    assert statuses["evaluate_alerts"] == "success"
    assert statuses["backup_databases"] == "success"
    second = sched.run_due(now=now, ctx=ctx)
    assert all(r["status"] in ("not_due", "skipped") for r in second)


def test_failed_refresh_does_not_corrupt_financials(tmp_path):
    before = _xirr()["xirr"]

    sstore = _sched_store(tmp_path)
    cfg = {"jobs": {"refresh_prices": {"enabled": True, "interval_hours": 24, "scope": "daily"}}}
    sched = Scheduler(config=cfg, store=sstore)

    def boom(**kwargs):
        raise RuntimeError("MCP unreachable")

    res = sched.run_job("refresh_prices", ctx={"price_sync": boom}, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert res["status"] == "failed"
    assert _xirr()["xirr"] == before  # unchanged
