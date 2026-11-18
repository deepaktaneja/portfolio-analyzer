"""Concrete refresh/backup/alert jobs (offline-safe, dependency-injectable).

Each job returns ``{"status", "rows", "details", "warnings"}`` and never raises —
failures are captured so a broken upstream can never corrupt portfolio data.
Network/LLM work is only attempted via injectable callables (``price_sync``,
``research_runner``) or the real fetchers; missing capability degrades to
``skipped`` with a reason rather than inventing data.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

STATUS_SUCCESS = "success"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


def _ok(rows=0, details=None, warnings=None):
    return {"status": STATUS_SUCCESS, "rows": int(rows or 0), "details": details or {}, "warnings": warnings or []}


def _skip(reason, details=None):
    return {"status": STATUS_SKIPPED, "rows": 0, "details": {"reason": reason, **(details or {})}, "warnings": [reason]}


def _fail(reason, details=None):
    return {"status": STATUS_FAILED, "rows": 0, "details": {"error": reason, **(details or {})}, "warnings": [reason]}


def _extract_rows(res) -> int:
    if isinstance(res, dict):
        for k in ("rows_inserted", "rows", "securities", "count", "updated"):
            if isinstance(res.get(k), int):
                return res[k]
        for v in res.values():
            if isinstance(v, int):
                return v
    return 0


def job_refresh_prices(ctx: dict | None = None) -> dict:
    """Daily equity price + MF NAV valuation refresh (incremental via provider cursors)."""
    ctx = ctx or {}
    sync = ctx.get("price_sync")
    if sync is None:
        try:
            from market_data.fetcher import sync_all_market_data as sync
        except Exception as exc:  # noqa: BLE001
            return _skip(f"price sync unavailable: {exc}")
    try:
        res = sync(
            db_path=ctx.get("market_db"),
            tx_db_path=ctx.get("tx_db"),
            provider=ctx.get("provider"),
            include_actions=False,
            include_dividends=False,
        )
        return _ok(_extract_rows(res), details={"sync": res if isinstance(res, dict) else {}})
    except Exception as exc:  # noqa: BLE001
        return _fail(f"price refresh failed: {exc}")


def job_refresh_corporate_actions(ctx: dict | None = None) -> dict:
    """Periodic corporate-action & dividend sync (verified entries preserved)."""
    ctx = ctx or {}
    sync = ctx.get("ca_sync")
    if sync is None:
        try:
            from market_data.fetcher import sync_all_market_data as sync
        except Exception as exc:  # noqa: BLE001
            return _skip(f"corporate-action sync unavailable: {exc}")
    try:
        res = sync(
            db_path=ctx.get("market_db"),
            tx_db_path=ctx.get("tx_db"),
            provider=ctx.get("provider"),
            include_actions=True,
            include_dividends=True,
        )
        return _ok(_extract_rows(res), details={"sync": res if isinstance(res, dict) else {}})
    except Exception as exc:  # noqa: BLE001
        return _fail(f"corporate-action refresh failed: {exc}")


def job_refresh_research(ctx: dict | None = None) -> dict:
    """Periodic AI research refresh for holdings with stale snapshots."""
    ctx = ctx or {}
    runner = ctx.get("research_runner")
    holdings = ctx.get("holdings") or []
    if runner is None:
        return _skip("research runner unavailable (LLM/MCP not configured)")
    if not holdings:
        return _skip("no holdings to research")
    limit = int(ctx.get("research_limit", 25))
    done = 0
    errors = 0
    for h in holdings[:limit]:
        try:
            report = runner(h)
            if isinstance(report, dict) and report.get("is_llm_available") is False:
                errors += 1
                continue
            done += 1
        except Exception:  # noqa: BLE001
            errors += 1
    return _ok(done, details={"attempted": min(len(holdings), limit), "errors": errors})


def job_evaluate_alerts(ctx: dict | None = None) -> dict:
    """Evaluate alert triggers and persist deduplicated alerts."""
    ctx = ctx or {}
    try:
        from alerts.store import AlertStore
        from alerts.triggers import build_alerts
    except Exception as exc:  # noqa: BLE001
        return _skip(f"alert subsystem unavailable: {exc}")
    alerts = ctx.get("alerts")
    if alerts is None:
        try:
            alerts = build_alerts(
                app_ctx=ctx.get("app_ctx"),
                decision_recs=ctx.get("decision_recs"),
                preferences=ctx.get("alert_prefs"),
                now=ctx.get("now"),
            )
        except Exception as exc:  # noqa: BLE001
            return _fail(f"alert evaluation failed: {exc}")
    try:
        store = ctx.get("alert_store") or AlertStore()
        kept = store.record_many(alerts, preferences=ctx.get("alert_prefs"))
        return _ok(len(kept), details={"generated": len(alerts), "stored": len(kept)})
    except Exception as exc:  # noqa: BLE001
        return _fail(f"alert persistence failed: {exc}")


def job_backup_databases(ctx: dict | None = None) -> dict:
    """Encrypted, verified backup of all SQLite databases."""
    ctx = ctx or {}
    key = ctx.get("backup_key") or os.environ.get("BACKUP_ENCRYPTION_KEY")
    if not key:
        return _skip("backup skipped: BACKUP_ENCRYPTION_KEY not configured")
    try:
        from security.backup import backup_all
    except Exception as exc:  # noqa: BLE001
        return _skip(f"backup subsystem unavailable: {exc}")
    try:
        created = backup_all(
            db_paths=ctx.get("db_paths"),
            dest_dir=ctx.get("backup_dir", "data/backups"),
            key=key,
        )
        return _ok(len(created), details={"files": created})
    except Exception as exc:  # noqa: BLE001
        return _fail(f"backup failed: {exc}")


def idempotency_key(scope: str, now: datetime | None = None) -> str:
    """Compute an idempotency key for the given scope (daily/hourly/monthly/quarterly)."""
    now = now or datetime.now(UTC)
    if scope == "hourly":
        return now.strftime("%Y-%m-%dT%H")
    if scope == "daily":
        return now.strftime("%Y-%m-%d")
    if scope == "weekly":
        iso = now.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if scope == "monthly":
        return now.strftime("%Y-%m")
    if scope == "quarterly":
        return f"{now.year}Q{(now.month - 1) // 3 + 1}"
    return now.strftime("%Y-%m-%d")

