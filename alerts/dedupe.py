"""Alert deduplication helpers: stable fingerprints + cooldown windows."""
from __future__ import annotations

from datetime import UTC, datetime

from alerts.models import Alert


def make_fingerprint(alert_type: str, security_id: str | None = None, dedupe_key: str | None = None) -> str:
    """Stable fingerprint for an alert condition (identical across repeated evaluations)."""
    a = Alert(alert_type=alert_type, severity="INFO", title="", message="",
              security_id=security_id, dedupe_key=dedupe_key)
    return a.fingerprint()


def parse_ts(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def within_cooldown(last_seen, cooldown_seconds: int, now: datetime | None = None) -> bool:
    """True if an alert with the same fingerprint was raised within the cooldown window."""
    if last_seen is None:
        return False
    last = parse_ts(last_seen)
    if last is None:
        return False
    now = now or datetime.now(UTC)
    return (now - last).total_seconds() < max(0, int(cooldown_seconds))


def passes_min_severity(severity: str, min_severity: str) -> bool:
    from alerts.models import severity_rank
    return severity_rank(severity) >= severity_rank(min_severity)
