"""Scheduler runner: register jobs, run due jobs with idempotency & recovery."""
from __future__ import annotations

import time
from datetime import UTC, datetime

from scheduler.config import load_schedule
from scheduler.jobs import (
    idempotency_key,
    job_backup_databases,
    job_evaluate_alerts,
    job_refresh_corporate_actions,
    job_refresh_prices,
    job_refresh_research,
)
from scheduler.store import JobStore

JOB_REGISTRY = {
    "refresh_prices": job_refresh_prices,
    "refresh_corporate_actions": job_refresh_corporate_actions,
    "refresh_research": job_refresh_research,
    "evaluate_alerts": job_evaluate_alerts,
    "backup_databases": job_backup_databases,
}


class Scheduler:
    """Runs configured jobs; idempotent per (job, scope-key) and recoverable."""

    def __init__(self, config: dict | None = None, store: JobStore | None = None):
        self.config = config or load_schedule()
        self.store = store or JobStore()

    def register(self, now: datetime | None = None) -> list[str]:
        names = []
        for name, cfg in self.config.get("jobs", {}).items():
            if name not in JOB_REGISTRY:
                continue
            self.store.upsert_job(
                name,
                interval_seconds=int(float(cfg.get("interval_hours", 24)) * 3600),
                enabled=bool(cfg.get("enabled", True)),
                now=now,
            )
            names.append(name)
        return names

    def recover(self, now: datetime | None = None) -> int:
        timeout = int(self.config.get("stale_run_timeout_minutes", 30)) * 60
        return self.store.recover_stale_runs(now=now, stale_after_seconds=timeout)

    def run_job(self, name: str, ctx: dict | None = None, now: datetime | None = None,
                force: bool = False, key: str | None = None) -> dict:
        cfg = self.config.get("jobs", {}).get(name, {})
        func = JOB_REGISTRY.get(name)
        if func is None:
            return {"job": name, "status": "failed", "error": f"unknown job '{name}'"}
        job_ctx = (ctx or {}).get(name, ctx or {}) if isinstance(ctx, dict) else {}
        k = key or idempotency_key(cfg.get("scope", "daily"), now)
        run_id = self.store.claim(name, k, now)
        if run_id is None:
            return {"job": name, "status": "skipped", "reason": "already completed for this period",
                    "key": k, "rows": 0}
        try:
            result = func(job_ctx)
        except Exception as exc:  # noqa: BLE001 — jobs must never crash the runner
            result = {"status": "failed", "rows": 0, "details": {"error": str(exc)}, "warnings": [str(exc)]}
        status = result.get("status", "failed")
        warnings = result.get("warnings") or []
        self.store.finish(run_id, status, rows_affected=result.get("rows", 0),
                          error=("; ".join(warnings) or None) if status == "failed" else None,
                          details=result.get("details"), now=now)
        return {"job": name, "status": status, "rows": result.get("rows", 0),
                "details": result.get("details", {}), "warnings": warnings, "key": k, "run_id": run_id}

    def run_due(self, now: datetime | None = None, ctx: dict | None = None) -> list[dict]:
        now = now or datetime.now(UTC)
        self.register(now=now)
        self.recover(now=now)
        results = []
        for name, cfg in self.config.get("jobs", {}).items():
            if name not in JOB_REGISTRY or not cfg.get("enabled", True):
                continue
            if self.store.is_due(name, now):
                results.append(self.run_job(name, ctx=ctx, now=now))
            else:
                results.append({"job": name, "status": "not_due"})
        return results

    def run_forever(self, interval_seconds: int = 300, ctx: dict | None = None) -> None:
        """Blocking loop for a long-running scheduler process (Ctrl-C to stop)."""
        try:
            while True:
                self.run_due(ctx=ctx)
                time.sleep(max(5, int(interval_seconds)))
        except KeyboardInterrupt:  # pragma: no cover - interactive stop
            return
