"""Alert delivery channels.

``in_app`` alerts are persisted by the store. ``email`` and ``webhook`` are
deliberate stubs that only log — no external notification is ever sent here,
honouring the Stage 10 constraint requiring explicit approval before any
external delivery is enabled.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger("alerts.channels")

_EXTERNAL_STUBS = {"email", "webhook", "slack", "sms"}


def deliver(alert, channels, now: datetime | None = None) -> list[dict]:
    """Record delivery attempts. Returns a list of {channel, status} dicts."""
    now_iso = (now or datetime.now(UTC)).isoformat()
    results = []
    for channel in channels or ["in_app"]:
        if channel == "in_app":
            results.append({"channel": "in_app", "status": "queued", "at": now_iso})
        elif channel in _EXTERNAL_STUBS:
            # Stub: log only. Never performs a network call.
            logger.info("alert %s/%s routed to %s (stub, not sent)",
                        alert.alert_type, alert.severity, channel)
            results.append({"channel": channel, "status": "stub_logged", "at": now_iso})
        else:
            results.append({"channel": channel, "status": "unknown_channel", "at": now_iso})
    return results
