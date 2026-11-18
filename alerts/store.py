"""SQLite-backed alert store with deduplication & cooldown (independent DB file)."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Boolean, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from alerts.dedupe import passes_min_severity, within_cooldown
from alerts.models import Alert, severity_rank
from alerts.preferences import resolve

ALERTS_DB_URL = os.getenv("ICICI_ALERTS_DB_PATH", "sqlite:///data/alerts.sqlite3")


def _iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat()


class AlertsBase(DeclarativeBase):
    """Independent declarative base for alert tables."""


class AlertRecord(AlertsBase):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    security_id: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    channels_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    suppressed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


_ENGINE = None
_SESSION_FACTORY = None


def get_alerts_engine(db_url: str | None = None):
    global _ENGINE
    url = db_url or ALERTS_DB_URL
    if _ENGINE is None or db_url is not None:
        if url.startswith("sqlite:///"):
            Path(url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, future=True)
        AlertsBase.metadata.create_all(engine)
        if db_url is None:
            _ENGINE = engine
        return engine
    return _ENGINE


def get_alerts_session(db_url: str | None = None):
    global _SESSION_FACTORY
    if db_url is not None or _SESSION_FACTORY is None:
        factory = sessionmaker(bind=get_alerts_engine(db_url), autoflush=False, autocommit=False, future=True)
        if db_url is None:
            _SESSION_FACTORY = factory
            return _SESSION_FACTORY()
        return factory()
    return _SESSION_FACTORY()


class AlertStore:
    """Repository for alert persistence + deduplication."""

    def __init__(self, session=None):
        self._session = session

    def _sess(self):
        return self._session or get_alerts_session()

    def _close(self, s):
        if self._session is None:
            s.close()

    def record(self, alert: Alert, preferences: dict | None = None, now: datetime | None = None) -> tuple[dict | None, bool]:
        """Persist an alert unless disabled/suppressed. Returns (stored_dict|None, suppressed)."""
        settings = resolve(preferences, alert)
        if not settings.get("enabled", True):
            return None, True
        if not passes_min_severity(alert.severity, settings.get("min_severity", "INFO")):
            return None, True

        fp = alert.fingerprint()
        s = self._sess()
        try:
            latest = (s.query(AlertRecord)
                      .filter_by(fingerprint=fp)
                      .order_by(AlertRecord.created_at.desc())
                      .first())
            if latest is not None and within_cooldown(latest.created_at, settings["cooldown_seconds"], now):
                latest.suppressed_count = (latest.suppressed_count or 0) + 1
                s.commit()
                return None, True

            channels = settings.get("channels", ["in_app"])
            delivered = []
            try:
                from alerts.channels import deliver
                delivered = deliver(alert, channels, now=now)
            except Exception:  # noqa: BLE001 — delivery is best-effort, never blocks storage
                delivered = [{"channel": c, "status": "stub"} for c in channels]

            rec = AlertRecord(
                alert_type=alert.alert_type, severity=alert.severity, security_id=alert.security_id,
                title=alert.title, message=alert.message,
                evidence_json=json.dumps(alert.evidence or {}, sort_keys=True, default=str),
                fingerprint=fp, created_at=alert.created_at or _iso(now),
                channels_json=json.dumps(delivered, default=str),
            )
            s.add(rec)
            s.commit()
            return self._to_dict(rec), False
        finally:
            self._close(s)

    def record_many(self, alerts, preferences: dict | None = None, now: datetime | None = None) -> list[dict]:
        stored = []
        for a in alerts or []:
            try:
                rec, _ = self.record(a, preferences=preferences, now=now)
                if rec is not None:
                    stored.append(rec)
            except Exception:  # noqa: BLE001, S112 — one bad alert must not block others
                continue
        return stored

    def list_alerts(self, limit: int = 200, severity: str | None = None,
                    active_only: bool = False) -> list[dict]:
        s = self._sess()
        try:
            q = s.query(AlertRecord)
            if severity:
                q = q.filter_by(severity=severity)
            if active_only:
                q = q.filter_by(acknowledged=False)
            rows = q.order_by(AlertRecord.created_at.desc()).limit(limit).all()
            return [self._to_dict(r) for r in rows]
        finally:
            self._close(s)

    def acknowledge(self, alert_id: int, now: datetime | None = None) -> bool:
        s = self._sess()
        try:
            rec = s.get(AlertRecord, alert_id)
            if rec is None:
                return False
            rec.acknowledged = True
            rec.acknowledged_at = _iso(now)
            s.commit()
            return True
        finally:
            self._close(s)

    def counts_by_severity(self) -> dict:
        s = self._sess()
        try:
            out = {"INFO": 0, "WARN": 0, "CRITICAL": 0}
            for rec in s.query(AlertRecord).all():
                out[rec.severity] = out.get(rec.severity, 0) + 1
            return out
        finally:
            self._close(s)

    @staticmethod
    def _to_dict(r: AlertRecord) -> dict:
        try:
            evidence = json.loads(r.evidence_json) if r.evidence_json else {}
        except ValueError:
            evidence = {}
        try:
            channels = json.loads(r.channels_json) if r.channels_json else []
        except ValueError:
            channels = []
        return {
            "id": r.id, "alert_type": r.alert_type, "severity": r.severity,
            "security_id": r.security_id, "title": r.title, "message": r.message,
            "evidence": evidence, "fingerprint": r.fingerprint, "created_at": r.created_at,
            "acknowledged": r.acknowledged, "acknowledged_at": r.acknowledged_at,
            "channels": channels, "suppressed_count": r.suppressed_count,
            "severity_rank": severity_rank(r.severity),
        }

