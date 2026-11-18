"""Alert domain models: severities, alert types, and the Alert dataclass."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

SEV_INFO = "INFO"
SEV_WARN = "WARN"
SEV_CRITICAL = "CRITICAL"
_SEV_RANK = {SEV_INFO: 0, SEV_WARN: 1, SEV_CRITICAL: 2}

# Alert types
A_SIGNAL_EXIT = "SIGNAL_EXIT"
A_SIGNAL_TRIM = "SIGNAL_TRIM"
A_SIGNAL_MONITOR = "SIGNAL_MONITOR"
A_CONCENTRATION = "CONCENTRATION"
A_VALUATION = "VALUATION"
A_GOVERNANCE = "GOVERNANCE"
A_EARNINGS = "EARNINGS"
A_STALE_DATA = "STALE_DATA"
A_IMPORT_FAILURE = "IMPORT_FAILURE"
A_RECONCILIATION = "RECONCILIATION"

ALL_TYPES = (
    A_SIGNAL_EXIT, A_SIGNAL_TRIM, A_SIGNAL_MONITOR,
    A_CONCENTRATION, A_VALUATION, A_GOVERNANCE, A_EARNINGS,
    A_STALE_DATA, A_IMPORT_FAILURE, A_RECONCILIATION,
)


def severity_rank(sev: str) -> int:
    return _SEV_RANK.get(sev, 0)


@dataclass
class Alert:
    """A single user-facing advisory alert (never an executed action)."""

    alert_type: str
    severity: str
    title: str
    message: str
    security_id: str | None = None
    dedupe_key: str | None = None
    evidence: dict = field(default_factory=dict)
    created_at: str | None = None
    channels: list[str] | None = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(UTC).isoformat()

    def fingerprint(self) -> str:
        payload = json.dumps(
            {"t": self.alert_type, "s": (self.security_id or "").upper(),
             "k": self.dedupe_key or self.severity},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {
            "alert_type": self.alert_type,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "security_id": self.security_id,
            "dedupe_key": self.dedupe_key,
            "evidence": self.evidence,
            "created_at": self.created_at,
            "channels": self.channels,
            "fingerprint": self.fingerprint(),
        }
