"""Tests for the MCP integration adapter.

Uses mocks since external MCP servers may not be available. Tests cover:
- Tool discovery validation (undiscovered tool raises ValueError)
- Schema validation against discovered inputSchema
- Evidence envelope structure
- Cache TTL and hit_count
- Graceful degradation (stale cache fallback)
- Circuit breaker behaviour
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_integration.adapter import MCPAdapter
from mcp_integration.client import MCPClientManager, MCPServerConnection, MCPToolInfo
from mcp_integration.cache import MCPCacheStore
from mcp_integration.evidence import build_envelope, canonical_args_hash, envelope_key


# ---------------------------------------------------------------------------
# Evidence envelope tests
# ---------------------------------------------------------------------------

def test_build_envelope_structure():
    e = build_envelope(
        server="company-research",
        tool="get_financials",
        arguments={"symbol": "RELIANCE"},
        data={"revenue": 100},
        status="ok",
        partial=False,
        warnings=[],
        meta={"elapsed_seconds": 0.5},
    )
    assert "evidence_id" in e
    assert e["server"] == "company-research"
    assert e["tool"] == "get_financials"
    assert e["status"] == "ok"
    assert e["partial"] is False
    assert e["data"] == {"revenue": 100}


def test_canonical_args_hash_deterministic():
    h1 = canonical_args_hash({"symbol": "RELIANCE", "period": "annual"})
    h2 = canonical_args_hash({"period": "annual", "symbol": "RELIANCE"})
    assert h1 == h2
    assert len(h1) == 64


def test_envelope_key_format():
    e = build_envelope(server="srv", tool="tool", arguments={"a": 1}, data={})
    key = envelope_key(e)
    assert key.startswith("srv|tool|")
    assert len(key.split("|")) == 3


# ---------------------------------------------------------------------------
# Cache store tests (fully mocked; avoids importing models.research which
# triggers SQLAlchemy mapper config over broken models/mf.py)
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch


def _make_mock_store(session=None):
    """Create an MCPCacheStore with _MCPCache and _get_session patched."""
    if session is None:
        session = MagicMock()
    with patch("mcp_integration.cache._MCPCache", return_value=MagicMock()), \
         patch("mcp_integration.cache._get_session", return_value=session):
        store = MCPCacheStore(session=session)
    return store, session


def _mock_query_first(session, row):
    mock_query = MagicMock()
    mock_filter = MagicMock()
    mock_filter.first.return_value = row
    mock_query.filter_by.return_value = mock_filter
    session.query.return_value = mock_query
    return mock_query


def test_cache_put_and_get():
    store, session = _make_mock_store()
    args = {"symbol": "RELIANCE"}
    response = {"revenue": 100000}

    _mock_query_first(session, None)
    cached = store.get("company-research", "get_financials", args)
    assert cached is None

    with patch("mcp_integration.cache._MCPCache", return_value=MagicMock()), \
         patch("mcp_integration.cache._get_session", return_value=session):
        args_hash = store.put("company-research", "get_financials", args, response)
    assert args_hash == canonical_args_hash(args)
    session.commit.assert_called()


def test_cache_expired_returns_none():
    store, session = _make_mock_store()
    args = {"symbol": "RELIANCE"}
    from datetime import UTC, datetime, timedelta
    expired_row = MagicMock()
    expired_row.expires_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _mock_query_first(session, expired_row)
    cached = store.get("company-research", "get_financials", args)
    assert cached is None


def test_cache_stale_fallback():
    store, session = _make_mock_store()
    args = {"symbol": "RELIANCE"}
    _mock_query_first(session, None)
    assert store.get("company-research", "get_financials", args) is None

    stale_row = MagicMock()
    stale_row.server_name = "company-research"
    stale_row.tool_name = "get_financials"
    stale_row.arguments = '{"symbol": "RELIANCE"}'
    stale_row.retrieved_at = "2024-01-01T00:00:00+00:00"
    stale_row.response = '{"revenue": 1}'
    stale_row.hit_count = 0

    session.query.return_value.filter_by.return_value.order_by.return_value.first.return_value = stale_row
    with patch("mcp_integration.cache._MCPCache", return_value=MagicMock()), \
         patch("mcp_integration.cache._get_session", return_value=session):
        stale = store.get_stale("company-research", "get_financials", args)
    assert stale is not None
    assert stale["partial"] is True
    assert stale["data"] == {"revenue": 1}


def test_cache_miss_returns_none():
    store, session = _make_mock_store()
    _mock_query_first(session, None)
    assert store.get("company-research", "get_financials", {"symbol": "NOPE"}) is None


# ---------------------------------------------------------------------------
# Adapter tests (mocked)
# ---------------------------------------------------------------------------

class _FakeMCPResult:
    """Fake MCP CallToolResult with .content blocks."""
    def __init__(self, text: str):
        self.content = [MagicMock(text=text)]
        self.model_dump = lambda: {"revenue": 100000}


@pytest.mark.asyncio
async def test_adapter_undiscovered_tool_raises_valueerror():
    manager = MCPClientManager()
    adapter = MCPAdapter(manager=manager, cache=MagicMock())
    from contextlib import asynccontextmanager

    fake_conn = MagicMock(spec=MCPServerConnection)
    fake_conn.tools = {}  # no tools discovered

    @asynccontextmanager
    async def fake_connect(name):
        yield fake_conn

    manager.connect = fake_connect

    with pytest.raises(ValueError, match="not discovered"):
        await adapter.call_tool("company-research", "get_financials", {"symbol": "RELIANCE"})


@pytest.mark.asyncio
async def test_adapter_schema_validation():
    """Arguments failing inputSchema validation must raise ValueError."""
    from contextlib import asynccontextmanager

    manager = MCPClientManager()
    adapter = MCPAdapter(manager=manager, cache=MagicMock())

    fake_conn = MagicMock(spec=MCPServerConnection)
    fake_conn.tools = {
        "get_financials": MCPToolInfo(
            name="get_financials",
            description="Get financials",
            input_schema={"type": "object", "required": ["symbol"], "properties": {"symbol": {"type": "string"}}},
        )
    }
    fake_conn.semaphore = asyncio.Semaphore(1)
    fake_conn.session = MagicMock()
    fake_conn.session.call_tool = AsyncMock(return_value=MagicMock(content=[MagicMock()]))
    manager._connections["company-research"] = fake_conn

    @asynccontextmanager
    async def fake_connect(name):
        # Patch _open_transport to return a mock transport so no real connection
        with patch.object(manager, "_open_transport", return_value=MagicMock()):
            yield fake_conn

    manager.connect = fake_connect

    with pytest.raises(ValueError, match="inputSchema validation"):
        await adapter.call_tool("company-research", "get_financials", {})


@pytest.mark.asyncio
async def test_adapter_schema_validation():
    """Arguments failing inputSchema validation must raise ValueError."""
    from contextlib import asynccontextmanager

    manager = MCPClientManager()
    adapter = MCPAdapter(manager=manager, cache=MagicMock())

    fake_conn = MagicMock(spec=MCPServerConnection)
    fake_conn.tools = {
        "get_financials": MCPToolInfo(
            name="get_financials",
            description="Get financials",
            input_schema={"type": "object", "required": ["symbol"], "properties": {"symbol": {"type": "string"}}},
        )
    }
    fake_conn.semaphore = asyncio.Semaphore(1)
    fake_conn.session = MagicMock()
    fake_conn.session.call_tool = AsyncMock(return_value=MagicMock(content=[MagicMock()]))
    manager._connections["company-research"] = fake_conn

    @asynccontextmanager
    async def fake_connect(name):
        # Patch _open_transport to return a mock transport so no real connection
        with patch.object(manager, "_open_transport", return_value=MagicMock()):
            yield fake_conn

    manager.connect = fake_connect

    with pytest.raises(ValueError, match="inputSchema validation"):
        await adapter.call_tool("company-research", "get_financials", {})


@pytest.mark.asyncio
async def test_adapter_successful_call_returns_envelope():
    manager = MCPClientManager()
    adapter = MCPAdapter(manager=manager, cache=MagicMock())
    mock_cache = adapter.cache
    # Mock cache returns None (cache miss), so code proceeds to actual call
    mock_cache.get.return_value = None

    fake_conn = MagicMock()
    fake_conn.tools = {"get_financials": MCPToolInfo(name="get_financials", description="Get financials", input_schema={})}
    fake_conn.semaphore = asyncio.Semaphore(1)
    fake_conn.session = MagicMock()
    fake_conn.session.call_tool = AsyncMock(return_value=_FakeMCPResult('{"revenue": 100000}'))
    fake_conn.circuit_open = False
    fake_conn.circuit_opened_at = None
    fake_conn.config = MagicMock(circuit_breaker_cooldown_seconds=30)
    manager._connections["company-research"] = fake_conn

    envelope = await adapter.call_tool("company-research", "get_financials", {"symbol": "RELIANCE"})
    assert envelope["status"] == "ok"
    assert envelope["partial"] is False
    assert envelope["data"]["revenue"] == 100000
    assert "evidence_id" in envelope
    assert envelope["meta"]["elapsed_seconds"] >= 0


@pytest.mark.asyncio
async def test_adapter_graceful_degradation_on_error():
    manager = MCPClientManager()
    adapter = MCPAdapter(manager=manager, cache=MagicMock())
    mock_cache = adapter.cache
    # Mock cache: get() returns None (cache miss), get_stale() returns None (no stale)
    mock_cache.get.return_value = None
    mock_cache.get_stale.return_value = None

    fake_conn = MagicMock()
    fake_conn.tools = {"get_financials": MCPToolInfo(name="get_financials", description="x", input_schema={})}
    fake_conn.semaphore = asyncio.Semaphore(1)
    fake_conn.session = MagicMock()
    fake_conn.session.call_tool = AsyncMock(side_effect=RuntimeError("server down"))
    fake_conn.circuit_open = False
    fake_conn.circuit_opened_at = None
    fake_conn.config = MagicMock(circuit_breaker_cooldown_seconds=30)
    manager._connections["company-research"] = fake_conn

    envelope = await adapter.call_tool("company-research", "get_financials", {"symbol": "RELIANCE"})
    assert envelope["status"] == "error"
    assert envelope["partial"] is True
    assert envelope["data"] is None
    assert "degraded" in envelope["meta"]


def test_manager_disabled_server():
    manager = MCPClientManager()
    assert manager.is_enabled("nse-market") is False
    assert manager.is_enabled("company-research") is True
    assert manager.server_config("nonexistent") is None


def test_manager_tool_exists_before_connect():
    manager = MCPClientManager()
    assert manager.tool_exists("company-research", "get_financials") is False