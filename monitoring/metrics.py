"""Monitoring metrics: scheduler, alerts, LLM usage, MCP cache & DB sizes.

``collect_metrics`` is defensive — any failing source degrades to an error string
without breaking the whole snapshot. ``to_prometheus_text`` emits a minimal,
dependency-free exposition format.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def _safe(fn, default):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)} if isinstance(default, dict) else default


def _scheduler_metrics() -> dict:
    from scheduler.store import JobStore
    store = JobStore()
    jobs = store.list_jobs()
    runs = store.get_recent_runs(limit=20)
    return {
        "jobs": jobs,
        "recent_runs": runs,
        "failed_runs": sum(1 for r in runs if r.get("status") == "failed"),
        "success_runs": sum(1 for r in runs if r.get("status") == "success"),
    }


def _alert_metrics() -> dict:
    from alerts.store import AlertStore
    store = AlertStore()
    return {"counts": store.counts_by_severity(), "total": len(store.list_alerts(limit=1000))}


def _usage_metrics() -> dict:
    from research.usage import UsageTracker
    return UsageTracker().totals()


def _mcp_cache_metrics() -> dict:
    from datetime import datetime as _dt

    from models.research import MCPCache, get_research_session
    s = get_research_session()
    try:
        rows = s.query(MCPCache).all()
        now = _dt.now(UTC)
        expired = 0
        hits = 0
        for r in rows:
            hits += r.hit_count or 0
            try:
                if _dt.fromisoformat(r.expires_at) < now:
                    expired += 1
            except ValueError:
                pass
        return {"entries": len(rows), "expired": expired, "total_hits": hits}
    finally:
        s.close()


_DB_FILES = {
    "xirr": "data/xirr.sqlite3",
    "market_data": "data/market_data.sqlite3",
    "research": "data/research.sqlite3",
    "decisions": "data/decisions.sqlite3",
    "mf_analytics": "data/mf_analytics.sqlite3",
    "scheduler": "data/scheduler.sqlite3",
    "alerts": "data/alerts.sqlite3",
    "usage": "data/usage.sqlite3",
}


def _db_sizes() -> dict:
    out = {}
    for name, path in _DB_FILES.items():
        p = Path(path)
        out[name] = p.stat().st_size if p.exists() else 0
    return out


def collect_metrics(mcp_health: list[dict] | None = None) -> dict:
    """Collect a full, defensive metrics snapshot."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scheduler": _safe(_scheduler_metrics, {}),
        "alerts": _safe(_alert_metrics, {}),
        "llm_usage": _safe(_usage_metrics, {}),
        "mcp_cache": _safe(_mcp_cache_metrics, {}),
        "databases": _safe(_db_sizes, {}),
        "mcp_health": mcp_health or [],
    }


def to_prometheus_text(metrics: dict) -> str:
    """Render metrics in Prometheus text exposition format (subset)."""
    lines: list[str] = []
    sched = metrics.get("scheduler") or {}
    jobs = sched.get("jobs") or []
    lines.append(f"portfolio_scheduler_jobs_total {len(jobs)}")
    lines.append(f"portfolio_scheduler_failed_runs {sched.get('failed_runs', 0)}")
    for j in jobs:
        status = j.get("last_status") or "never"
        lines.append(f'portfolio_scheduler_job{{job="{j.get("name")}",status="{status}"}} 1')

    alerts = metrics.get("alerts") or {}
    for sev, n in (alerts.get("counts") or {}).items():
        lines.append(f'portfolio_alerts_total{{severity="{sev}"}} {n}')

    usage = metrics.get("llm_usage") or {}
    if "total_tokens" in usage:
        lines.append(f"portfolio_llm_tokens_total {usage.get('total_tokens', 0)}")
        lines.append(f"portfolio_llm_cost_usd_total {usage.get('cost_usd', 0)}")

    cache = metrics.get("mcp_cache") or {}
    if "entries" in cache:
        lines.append(f"portfolio_mcp_cache_entries {cache.get('entries', 0)}")
        lines.append(f"portfolio_mcp_cache_expired {cache.get('expired', 0)}")

    for name, size in (metrics.get("databases") or {}).items():
        lines.append(f'portfolio_db_size_bytes{{db="{name}"}} {size}')

    for h in metrics.get("mcp_health") or []:
        status = 1 if h.get("status") == "ok" else 0
        lines.append(f'portfolio_mcp_health{{server="{h.get("server")}"}} {status}')
    return "\n".join(lines) + "\n"
