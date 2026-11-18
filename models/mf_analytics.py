"""Isolated storage for Stage 7 MF analytics (NAV cache, disclosures, fund metadata).

Uses an INDEPENDENT declarative base + engine (mirroring ``models/research.py``) so
it never participates in the mapper configuration of ``models/mf.py`` (which has
pre-existing relationship quirks) and keeps Stage 7 data self-contained in its own
SQLite file. Schema is self-provisioning via ``create_all``.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Index, Numeric, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

MF_ANALYTICS_DB_URL = os.getenv("ICICI_MF_ANALYTICS_DB_PATH", "sqlite:///data/mf_analytics.sqlite3")


class MFAnalyticsBase(DeclarativeBase):
    """Independent declarative base for MF analytics tables."""


class MFNavCache(MFAnalyticsBase):
    """Cached NAV history per scheme (source-tracked, freshness via as_of_date)."""

    __tablename__ = "mf_nav_cache"

    scheme_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    as_of_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    nav: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fetched_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("idx_mf_nav_cache_scheme", "scheme_code"),)


class MFSchemeHolding(MFAnalyticsBase):
    """A single stock holding disclosed by a scheme on a given as_of_date.

    These are *disclosed* holdings (AMFI/AMC factsheet). Missing/outdated
    disclosures are flagged by the overlap engine — values are never invented.
    """

    __tablename__ = "mf_scheme_holdings"

    scheme_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    as_of_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    stock_isin: Mapped[str] = mapped_column(String(12), primary_key=True)
    stock_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    weight_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(120), nullable=True)
    market_cap: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fetched_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("idx_mf_holdings_scheme_date", "scheme_code", "as_of_date"),)


class MFFundMeta(MFAnalyticsBase):
    """Scheme metadata: category, benchmark, expense ratios, AUM, manager."""

    __tablename__ = "mf_fund_metadata"

    scheme_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    scheme_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fund_house: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    benchmark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expense_ratio_regular: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)
    expense_ratio_direct: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)
    aum_crore: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    fund_manager: Mapped[str | None] = mapped_column(String(255), nullable=True)
    as_of_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fetched_at: Mapped[str | None] = mapped_column(Text, nullable=True)


_ENGINE = None
_SESSION_FACTORY = None


def get_mf_analytics_engine(db_url: str | None = None):
    """Return (and cache) the analytics engine, creating the schema on first use."""
    global _ENGINE
    url = db_url or MF_ANALYTICS_DB_URL
    if _ENGINE is None or db_url is not None:
        if url.startswith("sqlite:///"):
            db_path = url.replace("sqlite:///", "", 1)
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, future=True)
        MFAnalyticsBase.metadata.create_all(engine)
        if db_url is None:
            _ENGINE = engine
        return engine
    return _ENGINE


def get_mf_analytics_session(db_url: str | None = None):
    """Return a new session bound to the MF analytics database."""
    global _SESSION_FACTORY
    if db_url is not None or _SESSION_FACTORY is None:
        factory = sessionmaker(bind=get_mf_analytics_engine(db_url), autoflush=False, autocommit=False, future=True)
        if db_url is None:
            _SESSION_FACTORY = factory
            return _SESSION_FACTORY()
        return factory()
    return _SESSION_FACTORY()
