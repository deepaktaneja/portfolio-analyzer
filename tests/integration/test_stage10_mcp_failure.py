"""Stage 10 MCP failure & degradation integration tests.

Confirms failed MCP calls degrade gracefully (stale cache / partial envelope),
oversized responses are flagged and not cached, and accounting is never touched.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from mcp_integration.adapter import MCPAdapter


class FakeSession:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    async def call_tool(self, tool, arguments):
        if self._exc:
            raise self._exc
        return self._result


class FakeManager:
    def __init__(self, session):
        self._session = session

    def is_enabled(self, name):
        return True

    def server_config(self, name):
        return SimpleNamespace(enabled=True, timeout_seconds=1)

    def _record_success(self, conn):
        pass

    def _record_failure(self, conn):
        pass

    @asynccontextmanager
    async def connect(self, name):
        conn = SimpleNamespace(
            tools={"get_financials": SimpleNamespace(input_schema={})},
            session=self._session,
            semaphore=asyncio.Semaphore(1),
        )
        yield conn


class FakeCache:
    def __init__(self, stale=None):
        self._stale = stale
        self.puts = 0

    def get(self, *a, **k):
        return None

    def get_stale(self, *a, **k):
        return self._stale

    def put(self, *a, **k):
        self.puts += 1


def test_failed_call_serves_stale_cache():
    stale = {"status": "partial", "partial": True, "data": {"cached": True},
             "evidence_id": "e1", "warnings": ["served_from_stale_cache"]}
    adapter = MCPAdapter(manager=FakeManager(FakeSession(exc=RuntimeError("upstream down"))),
                         cache=FakeCache(stale=stale))
    res = asyncio.run(adapter.call_tool("srv", "get_financials", {}))
    assert res["status"] == "partial"
    assert res["data"] == {"cached": True}


def test_failed_call_without_cache_degrades_to_error():
    adapter = MCPAdapter(manager=FakeManager(FakeSession(exc=RuntimeError("boom"))), cache=FakeCache())
    res = asyncio.run(adapter.call_tool("srv", "get_financials", {}))
    assert res["status"] == "error"
    assert res["partial"] is True


def test_oversized_response_flagged_and_not_cached():
    big = {"content": "x" * 3_000_000}
    cache = FakeCache()
    adapter = MCPAdapter(manager=FakeManager(FakeSession(result=big)), cache=cache)
    res = asyncio.run(adapter.call_tool("srv", "get_financials", {}))
    assert res["partial"] is True
    assert any("oversized_response" in w for w in res["warnings"])
    assert cache.puts == 0


def test_undiscovered_tool_still_raises():
    adapter = MCPAdapter(manager=FakeManager(FakeSession(result={})), cache=FakeCache())
    try:
        asyncio.run(adapter.call_tool("srv", "not_a_tool", {}))
    except ValueError as exc:
        assert "not discovered" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for undiscovered tool")
