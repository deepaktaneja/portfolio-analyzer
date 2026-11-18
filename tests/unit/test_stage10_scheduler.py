"""Unit tests for the Stage 10 scheduler (idempotency, due logic, recovery)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scheduler.jobs import idempotency_key
from scheduler.runner import Scheduler
from scheduler.store import STATUS_FAILED, STATUS_SUCCESS, JobStore, SchedulerBase

CFG = {
    "stale_run_timeout_minutes": 30,
    "jobs": {"refresh_prices": {"enabled": True, "interval_hours": 24, "scope": "daily"}},
}


def make_store(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path/'sched.db'}", future=True)
    SchedulerBase.metadata.create_all(eng)
    return JobStore(sessionmaker(bind=eng, future=True)())


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


def test_claim_is_idempotent(tmp_path):
    store = make_store(tmp_path)
    rid = store.claim("job", "k1")
    assert rid is not None
    assert store.claim("job", "k1") is None  # duplicate suppressed


def test_finish_updates_status_and_next_run(tmp_path):
    store = make_store(tmp_path)
    store.upsert_job("job", 86400)
    rid = store.claim("job", "k1", now=_dt(2026, 1, 1))
    store.finish(rid, STATUS_SUCCESS, rows_affected=3, now=_dt(2026, 1, 1))
    job = store.get_job("job")
    assert job["last_status"] == STATUS_SUCCESS
    assert job["next_run_at"].startswith("2026-01-02")
    # not due same day, due next day
    assert store.is_due("job", _dt(2026, 1, 1)) is False
    assert store.is_due("job", _dt(2026, 1, 3)) is True


def test_failed_run_is_resumable(tmp_path):
    store = make_store(tmp_path)
    rid = store.claim("job", "k1")
    store.finish(rid, STATUS_FAILED, error="boom")
    resumed = store.claim("job", "k1")
    assert resumed is not None


def test_recover_stale_runs(tmp_path):
    store = make_store(tmp_path)
    store.claim("job", "k1", now=_dt(2026, 1, 1))
    recovered = store.recover_stale_runs(now=_dt(2026, 1, 1) + timedelta(hours=2), stale_after_seconds=1800)
    assert recovered == 1
    runs = store.get_recent_runs()
    assert runs[0]["status"] == STATUS_FAILED


def test_idempotency_key_scopes():
    now = datetime(2026, 5, 17, 9, 30, tzinfo=UTC)
    assert idempotency_key("daily", now) == "2026-05-17"
    assert idempotency_key("hourly", now) == "2026-05-17T09"
    assert idempotency_key("monthly", now) == "2026-05"
    assert idempotency_key("quarterly", now) == "2026Q2"


def test_scheduler_run_due_then_dedupe(tmp_path):
    store = make_store(tmp_path)
    sched = Scheduler(config=CFG, store=store)
    calls = {"n": 0}

    def fake_sync(**kwargs):
        calls["n"] += 1
        return {"rows_inserted": 2}

    now = _dt(2026, 3, 1)
    first = sched.run_due(now=now, ctx={"price_sync": fake_sync})
    assert any(r["status"] == "success" for r in first)
    assert calls["n"] == 1
    # Same day -> not_due / no further upstream calls (idempotent)
    second = sched.run_due(now=now, ctx={"price_sync": fake_sync})
    assert all(r["status"] in ("not_due", "skipped") for r in second)
    assert calls["n"] == 1


def test_job_failure_is_isolated(tmp_path):
    store = make_store(tmp_path)
    sched = Scheduler(config=CFG, store=store)

    def boom(**kwargs):
        raise RuntimeError("upstream down")

    res = sched.run_job("refresh_prices", ctx={"price_sync": boom}, now=_dt(2026, 3, 1))
    assert res["status"] == STATUS_FAILED
    assert "upstream down" in (res.get("warnings") or [""])[0]
