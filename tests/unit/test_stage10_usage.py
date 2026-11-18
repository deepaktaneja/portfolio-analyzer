"""Unit tests for Stage 10 LLM usage/cost tracking."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from research.llm import LLMClient, LLMResult
from research.usage import UsageBase, UsageTracker, estimate_tokens, price_for


def make_tracker(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path/'usage.db'}", future=True)
    UsageBase.metadata.create_all(eng)
    return UsageTracker(sessionmaker(bind=eng, future=True)())


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd" * 10) >= 10


def test_price_for():
    assert price_for("gemini-1.5-flash")[0] < price_for("unknown-model")[0]


def test_record_and_totals(tmp_path):
    tracker = make_tracker(tmp_path)
    tracker.record("gemini", "gemini-1.5-flash", prompt_tokens=1000, completion_tokens=500, elapsed_seconds=1.2)
    tracker.record("openrouter", "openrouter/auto", prompt_tokens=200, completion_tokens=100)
    totals = tracker.totals()
    assert totals["calls"] == 2
    assert totals["total_tokens"] == 1800
    assert totals["cost_usd"] > 0
    assert len(tracker.recent()) == 2


def test_llm_client_records_usage(tmp_path, monkeypatch):
    tracker = make_tracker(tmp_path)
    client = LLMClient()
    client.usage_tracker = tracker
    monkeypatch.setattr(client, "_call_gemini",
                        lambda s, u: LLMResult("hello world report", "gemini-1.5-flash", "gemini"))
    result = client.generate("system prompt", "user prompt")
    assert result.text == "hello world report"
    totals = tracker.totals()
    assert totals["calls"] == 1
    assert totals["total_tokens"] > 0
