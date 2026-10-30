"""SQLAlchemy database configuration for the portfolio analyzer."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("ICICI_XIRR_DB_PATH", "sqlite:///data/portfolio.sqlite3")


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def get_engine(database_url: str = DATABASE_URL):
    if database_url.startswith("sqlite:///"):
        db_path = database_url.replace("sqlite:///", "", 1)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(database_url, future=True)


engine = get_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_session():
    return SessionLocal()