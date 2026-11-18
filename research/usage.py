"""LLM usage & cost tracking (tokens, latency, estimated API cost).

Persists one row per LLM call to an independent SQLite file. Costs use a small,
configurable per-1M-token price table and are marked ``estimated`` so they are
never mistaken for a billed figure. Never records prompt/response text (privacy).
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Boolean, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

USAGE_DB_URL = os.getenv("ICICI_USAGE_DB_PATH", "sqlite:///data/usage.sqlite3")

# Approximate USD per 1M tokens (input, output). Estimates only — verify with provider.
PRICE_TABLE = {
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini": (0.075, 0.30),
    "openrouter/auto": (0.50, 1.50),
    "openrouter": (0.50, 1.50),
    "default": (0.50, 1.50),
}


def estimate_tokens(text: str | None) -> int:
    """Rough token estimate (~4 chars/token). Deterministic and vendor-agnostic."""
    if not text:
        return 0
    return max(1, len(str(text)) // 4)


def price_for(model: str | None) -> tuple[float, float]:
    m = (model or "").lower()
    for key, price in PRICE_TABLE.items():
        if key != "default" and key in m:
            return price
    return PRICE_TABLE["default"]


class UsageBase(DeclarativeBase):
    """Independent declarative base for LLM usage rows."""


class LLMCall(UsageBase):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    elapsed_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    estimated_cost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, index=True)


_ENGINE = None
_SESSION_FACTORY = None


def get_usage_engine(db_url: str | None = None):
    global _ENGINE
    url = db_url or USAGE_DB_URL
    if _ENGINE is None or db_url is not None:
        if url.startswith("sqlite:///"):
            Path(url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, future=True)
        UsageBase.metadata.create_all(engine)
        if db_url is None:
            _ENGINE = engine
        return engine
    return _ENGINE


def get_usage_session(db_url: str | None = None):
    global _SESSION_FACTORY
    if db_url is not None or _SESSION_FACTORY is None:
        factory = sessionmaker(bind=get_usage_engine(db_url), autoflush=False, autocommit=False, future=True)
        if db_url is None:
            _SESSION_FACTORY = factory
            return _SESSION_FACTORY()
        return factory()
    return _SESSION_FACTORY()


class UsageTracker:
    """Records and summarises LLM token usage/cost/latency."""

    def __init__(self, session=None):
        self._session = session

    def _sess(self):
        return self._session or get_usage_session()

    def _close(self, s):
        if self._session is None:
            s.close()

    def record(self, provider: str, model: str, prompt_tokens: int = 0, completion_tokens: int = 0,
               elapsed_seconds: float = 0.0, cost_usd: float | None = None,
               metadata: dict | None = None, now: datetime | None = None) -> dict:
        import json
        pt, ct = int(prompt_tokens or 0), int(completion_tokens or 0)
        estimated = cost_usd is None
        if estimated:
            in_price, out_price = price_for(model)
            cost_usd = (pt / 1_000_000.0) * in_price + (ct / 1_000_000.0) * out_price
        s = self._sess()
        try:
            row = LLMCall(provider=provider or "unknown", model=model or "unknown",
                          prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct,
                          elapsed_seconds=float(elapsed_seconds or 0.0), cost_usd=round(float(cost_usd), 6),
                          estimated_cost=estimated,
                          metadata_json=json.dumps(metadata or {}, sort_keys=True, default=str),
                          created_at=(now or datetime.now(UTC)).isoformat())
            s.add(row)
            s.commit()
            return {"provider": row.provider, "model": row.model, "total_tokens": row.total_tokens,
                    "cost_usd": row.cost_usd, "estimated_cost": estimated}
        finally:
            self._close(s)

    def totals(self) -> dict:
        s = self._sess()
        try:
            rows = s.query(LLMCall).all()
            return {
                "calls": len(rows),
                "prompt_tokens": sum(r.prompt_tokens for r in rows),
                "completion_tokens": sum(r.completion_tokens for r in rows),
                "total_tokens": sum(r.total_tokens for r in rows),
                "cost_usd": round(sum(r.cost_usd for r in rows), 6),
                "elapsed_seconds": round(sum(r.elapsed_seconds for r in rows), 3),
            }
        finally:
            self._close(s)

    def recent(self, limit: int = 50) -> list[dict]:
        s = self._sess()
        try:
            rows = s.query(LLMCall).order_by(LLMCall.created_at.desc()).limit(limit).all()
            return [{"provider": r.provider, "model": r.model, "tokens": r.total_tokens,
                     "cost_usd": r.cost_usd, "elapsed_seconds": r.elapsed_seconds,
                     "created_at": r.created_at} for r in rows]
        finally:
            self._close(s)

