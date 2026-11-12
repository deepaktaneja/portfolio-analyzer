"""SQLAlchemy ORM models for equity transaction ledger (Stage 1).

Implements the database schema approved in Stage 0 (docs/data_model.md):
portfolios, securities, transactions, holdings, import_audit.

All monetary columns use SQLAlchemy ``Numeric`` (Decimal) for exact precision.
Dates are stored as ISO-8601 TEXT. The design is extensible for mutual funds
and corporate actions in later stages.
"""

from __future__ import annotations

import uuid
from datetime import datetime, UTC
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from models.db import Base


class Portfolio(Base):
    """Top-level portfolio container. Scopes all transactions/holdings."""

    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=lambda: datetime.now(UTC).isoformat()
    )

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )
    holdings: Mapped[list["Holding"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )
    import_audits: Mapped[list["ImportAudit"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class Security(Base):
    """Security master. ISIN is the preferred natural key for equities."""

    __tablename__ = "securities"

    isin: Mapped[str] = mapped_column(String(12), primary_key=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    exchange: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    sector: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    instrument_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="EQUITY"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="security")
    holdings: Mapped[list["Holding"]] = relationship(back_populates="security")


class Transaction(Base):
    """Normalised transaction ledger row. One row per buy/sell/opening event."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.isin", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trade_date: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    settlement_date: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    side: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 6), nullable=True
    )
    gross_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    brokerage: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    gst: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    charges: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    source_file: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_priority: Mapped[str] = mapped_column(
        String(30), nullable=False, default="historical_statement", index=True
    )
    fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    imported_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=lambda: datetime.now(UTC).isoformat()
    )
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal("1.00"), nullable=False
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="transactions")
    security: Mapped["Security"] = relationship(back_populates="transactions")

    __table_args__ = (
        Index("idx_tx_portfolio_security_date", "portfolio_id", "security_id", "trade_date"),
    )


class Holding(Base):
    """Derived current position (recomputed from FIFO lots, not source of truth)."""

    __tablename__ = "holdings"

    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    security_id: Mapped[str] = mapped_column(
        ForeignKey("securities.isin", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    cost_basis: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=lambda: datetime.now(UTC).isoformat()
    )

    portfolio: Mapped["Portfolio"] = relationship(back_populates="holdings")
    security: Mapped["Security"] = relationship(back_populates="holdings")


class ImportAudit(Base):
    """Audit trail for every statement import."""

    __tablename__ = "import_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="imported", nullable=False)
    imported_at: Mapped[str] = mapped_column(
        Text, nullable=False, default=lambda: datetime.now(UTC).isoformat()
    )

    portfolio: Mapped["Portfolio"] = relationship(back_populates="import_audits")

