"""SQLite-backed scheduler store: job registry, run tracking, idempotency, recovery.

Uses an independent declarative base/engine (mirroring models/research.py) so it
never participates in the shared models.db.Base and lives in its own SQLite file.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

SCHEDULER_DB_URL = os.getenv("ICICI_SCHEDULER_DB_PATH", "sqlite:///data/scheduler.sqlite3")

STATUS_SUCCESS = "success"
STATUS_RUNNING = "running"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat()


class SchedulerBase(DeclarativeBase):
    """Independent declarative base for scheduler tables."""


class ScheduledJob(SchedulerBase):
    __tablename__ = "scheduled_jobs"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=86400)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    next_run_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class JobRun(SchedulerBase):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_RUNNING)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    rows_affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (UniqueConstraint("job_name", "idempotency_key", name="uq_job_key"),)


_ENGINE = None
_SESSION_FACTORY = None


def get_scheduler_engine(db_url: str | None = None):
    global _ENGINE
    url = db_url or SCHEDULER_DB_URL
    if _ENGINE is None or db_url is not None:
        if url.startswith("sqlite:///"):
            Path(url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, future=True)
        SchedulerBase.metadata.create_all(engine)
        if db_url is None:
            _ENGINE = engine
        return engine
    return _ENGINE


def get_scheduler_session(db_url: str | None = None):
    global _SESSION_FACTORY
    if db_url is not None or _SESSION_FACTORY is None:
        factory = sessionmaker(bind=get_scheduler_engine(db_url), autoflush=False, autocommit=False, future=True)
        if db_url is None:
            _SESSION_FACTORY = factory
            return _SESSION_FACTORY()
        return factory()
    return _SESSION_FACTORY()


class JobStore:
    """Repository providing idempotent job claiming, completion and recovery."""

    def __init__(self, session=None):
        self._session = session

    def _sess(self):
        return self._session or get_scheduler_session()

    def _close(self, s):
        if self._session is None:
            s.close()

    # -- registry -------------------------------------------------------
    def upsert_job(self, name: str, interval_seconds: int, enabled: bool = True, now=None) -> None:
        s = self._sess()
        try:
            row = s.get(ScheduledJob, name)
            if row is None:
                row = ScheduledJob(name=name, interval_seconds=int(interval_seconds),
                                   enabled=bool(enabled), updated_at=_now_iso(now))
                s.add(row)
            else:
                row.interval_seconds = int(interval_seconds)
                row.enabled = bool(enabled)
                row.updated_at = _now_iso(now)
            s.commit()
        finally:
            self._close(s)

    def get_job(self, name: str) -> dict | None:
        s = self._sess()
        try:
            row = s.get(ScheduledJob, name)
            if row is None:
                return None
            return {"name": row.name, "interval_seconds": row.interval_seconds, "enabled": row.enabled,
                    "last_run_at": row.last_run_at, "last_status": row.last_status, "next_run_at": row.next_run_at}
        finally:
            self._close(s)

    def list_jobs(self) -> list[dict]:
        s = self._sess()
        try:
            return [{"name": r.name, "interval_seconds": r.interval_seconds, "enabled": r.enabled,
                     "last_run_at": r.last_run_at, "last_status": r.last_status, "next_run_at": r.next_run_at}
                    for r in s.query(ScheduledJob).order_by(ScheduledJob.name).all()]
        finally:
            self._close(s)

    # -- due scheduling -------------------------------------------------
    def is_due(self, name: str, now: datetime | None = None) -> bool:
        job = self.get_job(name)
        if job is None:
            return True
        if not job["enabled"]:
            return False
        if not job["last_run_at"]:
            return True
        try:
            last = datetime.fromisoformat(job["last_run_at"])
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        return ((now or datetime.now(UTC)) - last).total_seconds() >= int(job["interval_seconds"])


    # -- idempotent claim / finish --------------------------------------
    def claim(self, job_name: str, idempotency_key: str, now: datetime | None = None) -> int | None:
        """Insert a running row unless this (job,key) already exists. Returns run id or None."""
        s = self._sess()
        try:
            existing = (s.query(JobRun)
                        .filter_by(job_name=job_name, idempotency_key=idempotency_key)
                        .first())
            if existing is not None:
                # Resume only a previous failed attempt (recoverable); else idempotent skip.
                if existing.status == STATUS_FAILED:
                    existing.status = STATUS_RUNNING
                    existing.started_at = _now_iso(now)
                    existing.finished_at = None
                    existing.error = None
                    s.commit()
                    return existing.id
                return None
            run = JobRun(job_name=job_name, idempotency_key=idempotency_key,
                         status=STATUS_RUNNING, started_at=_now_iso(now))
            s.add(run)
            s.commit()
            return run.id
        except IntegrityError:
            s.rollback()
            return None
        finally:
            self._close(s)

    def finish(self, run_id: int, status: str, rows_affected: int = 0, error: str | None = None,
               details: dict | None = None, now: datetime | None = None) -> None:
        import json
        s = self._sess()
        try:
            run = s.get(JobRun, run_id)
            if run is None:
                return
            run.status = status
            run.finished_at = _now_iso(now)
            run.rows_affected = int(rows_affected or 0)
            run.error = error
            run.details_json = json.dumps(details or {}, sort_keys=True, default=str)
            job = s.get(ScheduledJob, run.job_name)
            if job is not None and status in (STATUS_SUCCESS, STATUS_SKIPPED, STATUS_FAILED):
                job.last_run_at = _now_iso(now)
                job.last_status = status
                job.updated_at = _now_iso(now)
                if status != STATUS_FAILED:
                    from datetime import timedelta
                    job.next_run_at = (datetime.fromisoformat(job.last_run_at)
                                       + timedelta(seconds=int(job.interval_seconds))).isoformat()
            s.commit()
        finally:
            self._close(s)

    def recover_stale_runs(self, now: datetime | None = None, stale_after_seconds: int = 1800) -> int:
        """Mark long-running 'running' rows as failed so they can be retried."""
        now = now or datetime.now(UTC)
        s = self._sess()
        recovered = 0
        try:
            for run in s.query(JobRun).filter_by(status=STATUS_RUNNING).all():
                try:
                    started = datetime.fromisoformat(run.started_at)
                except ValueError:
                    started = now
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                if (now - started).total_seconds() >= stale_after_seconds:
                    run.status = STATUS_FAILED
                    run.finished_at = now.isoformat()
                    run.error = "recovered: stale running job (interrupted)"
                    recovered += 1
            s.commit()
            return recovered
        finally:
            self._close(s)

    def get_recent_runs(self, limit: int = 50) -> list[dict]:
        import json
        s = self._sess()
        try:
            rows = s.query(JobRun).order_by(JobRun.started_at.desc()).limit(limit).all()
            out = []
            for r in rows:
                try:
                    details = json.loads(r.details_json) if r.details_json else {}
                except ValueError:
                    details = {}
                out.append({"id": r.id, "job_name": r.job_name, "idempotency_key": r.idempotency_key,
                            "status": r.status, "started_at": r.started_at, "finished_at": r.finished_at,
                            "rows_affected": r.rows_affected, "error": r.error, "details": details})
            return out
        finally:
            self._close(s)

