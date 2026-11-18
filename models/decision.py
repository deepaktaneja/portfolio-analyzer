"""SQLAlchemy ORM models for the Stage 8 decision engine (signals & recommendations).

Uses an INDEPENDENT declarative base + engine (mirroring ``models/research.py`` and
``models/mf_analytics.py``) so it never participates in the shared ``models.db.Base``
mapper configuration and keeps decision data in its own SQLite file. Self-provisioning.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Boolean, Float, Index, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DECISION_DB_URL = os.getenv("ICICI_DECISION_DB_PATH", "sqlite:///data/decisions.sqlite3")


class DecisionBase(DeclarativeBase):
    """Independent declarative base for decision-engine tables."""


class DecisionSignal(DecisionBase):
    """A single explainable signal produced for a holding/scheme (shadow mode)."""

    __tablename__ = "decision_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    security_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    security_type: Mapped[str] = mapped_column(String(10), nullable=False, default="equity")
    signal_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # INFO/WARN/CRITICAL
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    thresholds_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    triggered_at: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    acknowledged_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("idx_signal_security", "security_id", "signal_type"),)


class DecisionRecommendation(DecisionBase):
    """An explainable recommendation combining rules (+ optional LLM context)."""

    __tablename__ = "decision_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    security_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    security_type: Mapped[str] = mapped_column(String(10), nullable=False, default="equity")
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    signals_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    upside_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    downside_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_case_cagr: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    llm_opinion_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_shadow: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    acknowledged_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_decision: Mapped[str | None] = mapped_column(String(30), nullable=True)

    __table_args__ = (Index("idx_rec_security", "security_id", "created_at"),)


class DecisionHistory(DecisionBase):
    """Recorded user decisions + subsequent outcomes (for evaluation)."""

    __tablename__ = "decision_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    security_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    user_decision: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[str] = mapped_column(Text, nullable=False)
    price_at_decision: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_3m: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_6m: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_12m: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)


class DecisionBacktest(DecisionBase):
    """Stored backtest run configuration + aggregate performance results."""

    __tablename__ = "decision_backtests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    start_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    end_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    false_positive_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    missed_upside_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


_ENGINE = None
_SESSION_FACTORY = None


def get_decision_engine(db_url: str | None = None):
    """Return (and cache) the decision engine, creating the schema on first use."""
    global _ENGINE
    url = db_url or DECISION_DB_URL
    if _ENGINE is None or db_url is not None:
        if url.startswith("sqlite:///"):
            db_path = url.replace("sqlite:///", "", 1)
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, future=True)
        DecisionBase.metadata.create_all(engine)
        if db_url is None:
            _ENGINE = engine
        return engine
    return _ENGINE


def get_decision_session(db_url: str | None = None):
    """Return a new session bound to the decision database."""
    global _SESSION_FACTORY
    if db_url is not None or _SESSION_FACTORY is None:
        factory = sessionmaker(bind=get_decision_engine(db_url), autoflush=False, autocommit=False, future=True)
        if db_url is None:
            _SESSION_FACTORY = factory
            return _SESSION_FACTORY()
        return factory()
    return _SESSION_FACTORY()
