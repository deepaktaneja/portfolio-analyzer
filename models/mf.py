"""SQLAlchemy ORM models for mutual funds (Stage 3)."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.db import Base


class MFScheme(Base):
    """MF scheme master; AMFI scheme_code is the natural key."""

    __tablename__ = "mf_schemes"

    scheme_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    scheme_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    plan_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    direct_regular: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    growth_idcw: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    nav: Mapped[list["MFNav"]] = relationship("MFNav", back_populates="scheme", cascade="all, delete-orphan")
    transactions: Mapped[list["MFTransaction"]] = relationship("MFTransaction", back_populates="scheme", cascade="all, delete-orphan")
    folios: Mapped[list["MFPortfolio"]] = relationship("MFPortfolio", back_populates="scheme", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("scheme_name", "plan_type", "direct_regular", name="uq_scheme_name_plan"),)
    scheme_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    plan_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    direct_regular: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    growth_idcw: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    nav: Mapped[list["MFNav"]] = relationship("MFNav", back_populates="scheme", cascade="all, delete-orphan")
    transactions: Mapped[list["MFTransaction"]] = relationship("MFTransaction", back_populates="scheme", cascade="all, delete-orphan")
    folios: Mapped[list["MFPortfolio"]] = relationship("MFPortfolio", back_populates="scheme", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("scheme_name", "plan_type", "direct_regular", name="uq_scheme_name_plan"),)
    nav: Mapped[list["MFNav"]] = relationship("MFNav", back_populates="scheme", cascade="all, delete-orphan")
    transactions: Mapped[list["MFTransaction"]] = relationship("MFTransaction", back_populates="scheme", cascade="all, delete-orphan")
    folios: Mapped[list["MFPortfolio"]] = relationship("MFPortfolio", back_populates="scheme", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("scheme_name", "plan_type", "direct_regular", name="uq_scheme_name_plan"),)

class MFPlan(Base):
    """Plan-level detail; Growth/IDCW, Direct/Regular."""
    __tablename__ = "mf_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheme_code: Mapped[str] = mapped_column(ForeignKey("mf_schemes.scheme_code", ondelete="CASCADE"), nullable=False, index=True)
    plan_name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    direct_regular: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    growth_idcw: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    nav_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    nav: Mapped[list["MFNav"]] = relationship("MFNav", back_populates="plan", cascade="all, delete-orphan")
    transactions: Mapped[list["MFTransaction"]] = relationship("MFTransaction", back_populates="plan", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("scheme_code", "plan_name", name="uq_plan_scheme_name"),)
    nav: Mapped[list["MFNav"]] = relationship("MFNav", back_populates="plan", cascade="all, delete-orphan")
    transactions: Mapped[list["MFTransaction"]] = relationship("MFTransaction", back_populates="plan", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("scheme_code", "plan_name", name="uq_plan_scheme_name"),)

class MFNav(Base):
    """Historical NAV for scheme/plan; NAV is the source of truth for value."""
    __tablename__ = "mf_nav"

    scheme_code: Mapped[str] = mapped_column(ForeignKey("mf_schemes.scheme_code", ondelete="CASCADE"), nullable=False, index=True)
    plan_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    as_of_date: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    nav: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    fetched_at: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    compressed_nav: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)

    scheme: Mapped[Optional[MFScheme]] = relationship(back_populates="nav")
    plan: Mapped[Optional[MFPlan]] = relationship(back_populates="nav")

    __table_args__ = (UniqueConstraint("scheme_code", "plan_type", "as_of_date", name="uq_nav_scheme_date"),)

class MFTransaction(Base):
    """MF transaction: SIP, lump-sum, redemption, switch, STP, SWP, IDCW."""
    __tablename__ = "mf_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("mf_portfolios.id", ondelete="CASCADE"), nullable=False, index=True)
    scheme_code: Mapped[Optional[str]] = mapped_column(ForeignKey("mf_schemes.scheme_code", ondelete="CASCADE"), nullable=True, index=True)
    plan_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    direct_regular: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    growth_idcw: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    trade_date: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(20), nullable=False)
    units: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    nav: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    source_file: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_priority: Mapped[str] = mapped_column(String(30), nullable=False, server_default="mf_statement")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    imported_at: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    scheme: Mapped[Optional[MFScheme]] = relationship(back_populates="transactions")
    plan: Mapped[Optional[MFPlan]] = relationship(back_populates="transactions")
    portfolio: Mapped[Optional["MFPortfolio"]] = relationship(back_populates="transactions")

    __table_args__ = (Index("idx_mf_portfolio_scheme_date", "portfolio_id", "scheme_code", "trade_date"),)

class MFPortfolio(Base):
    """Scopes MF transactions/holdings; one per equity portfolio."""
    __tablename__ = "mf_portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    equity_portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True)
    scheme_code: Mapped[str] = mapped_column(ForeignKey("mf_schemes.scheme_code", ondelete="CASCADE"), nullable=False, index=True)
    plan_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    direct_regular: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    transactions: Mapped[list["MFTransaction"]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")

    scheme: Mapped[Optional[MFScheme]] = relationship("MFScheme", back_populates="folios")

    __table_args__ = (UniqueConstraint("equity_portfolio_id", "scheme_code", name="uq_mf_portfolio_equity"),)
