"""SQLAlchemy ORM models for research and MCP evidence (Stage 6).

Implements the data model approved in docs/data_model.md:
- mcp_cache: evidence envelopes for every MCP call (idempotent via args_hash + TTL)
- research_reports: LLM-generated research reports with evidence citations
- research_snapshots: historical snapshots of research outlook per holding

All dates stored as ISO-8601 TEXT (UTC). Monetary values use Decimal/Numeric.
No changes to any other application's database.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

# Research models use an INDEPENDENT declarative base + engine so they never
# participate in the shared models.db.Base mapper configuration. This isolates
# Stage 6 research persistence from pre-existing Stage 3 MF model issues and
# keeps research data in its own database file.
RESEARCH_DB_URL = os.getenv("ICICI_RESEARCH_DB_PATH", "sqlite:///data/research.sqlite3")


class ResearchBase(DeclarativeBase):
    """Independent declarative base for Stage 6 research + MCP evidence tables."""


class MCPCache(ResearchBase):
    """MCP evidence cache: one row per (server, tool, args_hash).

    args_hash = SHA-256 of canonicalised (sorted-key JSON) arguments.
    TTL governed by expires_at; hit_count tracks cache reuse.
    """

    __tablename__ = "mcp_cache"

    server_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    args_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    arguments: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    response: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    retrieved_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("idx_mcp_cache_expires", "expires_at"),
    )


class ResearchReport(ResearchBase):
    """LLM-generated research report, evidence-cited via mcp_cache evidence_ids.

    content_md holds the full Markdown report. evidence_ids is a JSON array
    of mcp_cache composite keys (server|tool|args_hash) cited in the report.
    """

    __tablename__ = "research_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    security_id: Mapped[str | None] = mapped_column(
        String(12), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    generated_at: Mapped[str] = mapped_column(Text, nullable=False)

    snapshots: Mapped[list[ResearchSnapshot]] = relationship(
        "ResearchSnapshot", back_populates="report", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_research_reports_portfolio", "portfolio_id"),
        Index("idx_research_reports_security", "security_id"),
        Index("idx_research_reports_generated", "generated_at"),
    )


class ResearchSnapshot(ResearchBase):
    """Historical snapshot of research outlook for a security.

    Captures key metrics and outlook at a point in time so changes in
    fundamentals and investment outlook can be tracked across snapshots.
    """

    __tablename__ = "research_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("research_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    security_id: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    snapshot_date: Mapped[str] = mapped_column(Text, nullable=False)
    key_metrics_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    outlook: Mapped[str] = mapped_column(String(50), nullable=False)  # bull/base/bear/neutral
    generated_at: Mapped[str] = mapped_column(Text, nullable=False)

    report: Mapped[ResearchReport] = relationship(back_populates="snapshots")

    __table_args__ = (
        Index("idx_research_snapshots_security_date", "security_id", "snapshot_date"),
    )


_ENGINE = None
_SESSION_FACTORY = None


def get_research_engine(db_url: str | None = None):
    """Return (and cache) the engine for the research database, creating schema."""
    global _ENGINE
    url = db_url or RESEARCH_DB_URL
    if _ENGINE is None or db_url is not None:
        if url.startswith("sqlite:///"):
            db_path = url.replace("sqlite:///", "", 1)
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, future=True)
        ResearchBase.metadata.create_all(engine)
        if db_url is None:
            _ENGINE = engine
        return engine
    return _ENGINE


def get_research_session(db_url: str | None = None):
    """Return a new SQLAlchemy session bound to the research database."""
    global _SESSION_FACTORY
    if db_url is not None or _SESSION_FACTORY is None:
        engine = get_research_engine(db_url)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        if db_url is None:
            _SESSION_FACTORY = factory
            return _SESSION_FACTORY()
        return factory()
    return _SESSION_FACTORY()