"""Unit tests for Stage 10 reliability helpers (rate limiting, retries, health)."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import ClassVar

from mcp_integration.health import health_check, retry_async
from mcp_integration.rate_limiter import RateLimiterRegistry, TokenBucket


def test_token_bucket_refill():
    clock = [0.0]
    tb = TokenBucket(rate=1.0, capacity=2, clock=lambda: clock[0])
    assert tb.try_acquire() is True
    assert tb.try_acquire() is True
    assert tb.try_acquire() is False
    clock[0] = 1.0
    assert tb.try_acquire() is True


def test_token_bucket_wait():
    tb = TokenBucket(rate=2.0, capacity=1, clock=lambda: 0.0)
    assert tb.try_acquire() is True
    assert tb.wait_seconds() == 0.5


def test_rate_limiter_registry():
    reg = RateLimiterRegistry(default_rate=100, default_capacity=1)
    assert reg.try_acquire("srv") is True
    assert reg.try_acquire("srv") is False


def test_retry_async_succeeds_after_failures():
    attempts = {"n": 0}

    async def factory():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("transient")
        return "ok"

    assert asyncio.run(retry_async(factory, retries=3, base_delay=0)) == "ok"
    assert attempts["n"] == 2


def test_health_check_ok():
    class Conn:
        tools: ClassVar[dict] = {"get_financials": object()}

    class Mgr:
        def server_config(self, name):
            return type("C", (), {"enabled": True, "timeout_seconds": 5})()

        @asynccontextmanager
        async def connect(self, name):
            yield Conn()

    res = asyncio.run(health_check(Mgr(), "srv"))
    assert res["status"] == "ok"
    assert res["tools"] == 1


def test_health_check_error():
    class Mgr:
        def server_config(self, name):
            return type("C", (), {"enabled": True, "timeout_seconds": 5})()

        @asynccontextmanager
        async def connect(self, name):
            raise RuntimeError("unreachable")
            yield  # pragma: no cover

    res = asyncio.run(health_check(Mgr(), "srv"))
    assert res["status"] == "error"
    assert "unreachable" in res["error"]
