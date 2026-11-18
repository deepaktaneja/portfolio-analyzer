"""MCP evidence cache: read/write to the mcp_cache table.

Implements args_hash idempotency, TTL by capability class, hit_count tracking,
and graceful degradation (stale cache or partial envelope on error).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from mcp_integration.evidence import canonical_args_hash, envelope_key


def _get_session():
    from models.research import get_research_session
    return get_research_session()


def _MCPCache():
    from models.research import MCPCache
    return MCPCache


class MCPCacheStore:
    """Thin repository over the mcp_cache table."""

    # TTL by capability class (seconds). Overridable via settings.
    TTL_BY_CAPABILITY: ClassVar[dict[str, int]] = {
        "historical_prices": 7 * 24 * 3600,      # 7 days
        "index_history": 7 * 24 * 3600,           # 7 days
        "quarterly_earnings": 1 * 24 * 3600,      # 1 day
        "financial_statements": 1 * 24 * 3600,    # 1 day
        "financial_ratios": 1 * 24 * 3600,        # 1 day
        "current_prices": 60 * 60,                # 1 hour
        "analyst_research": 4 * 60 * 60,          # 4 hours
        "default": 1 * 24 * 3600,                 # 1 day
    }

    def __init__(self, session=None):
        self._session = session

    @contextmanager
    def _session_ctx(self):
        if self._session is not None:
            yield self._session
            return
        s = _get_session()
        try:
            yield s
        finally:
            s.close()

    def _ttl_for(self, tool_name: str) -> int:
        tl = self.TTL_BY_CAPABILITY["default"]
        for key, ttl in self.TTL_BY_CAPABILITY.items():
            if key in tool_name.lower():
                tl = ttl
                break
        return tl

    def get(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return cached envelope if present and not expired, else None.

        Increments hit_count on a hit.
        """
        args_hash = canonical_args_hash(arguments)
        with self._session_ctx() as s:
            MCPCache = _MCPCache()
            row = (
                s.query(MCPCache)
                .filter_by(server_name=server_name, tool_name=tool_name, args_hash=args_hash)
                .first()
            )
            if row is None:
                return None
            expires_at = datetime.fromisoformat(row.expires_at)
            if datetime.now(UTC) > expires_at:
                return None  # expired
            row.hit_count = (row.hit_count or 0) + 1
            s.commit()
            return {
                "evidence_id": envelope_key(
                    {
                        "server": server_name,
                        "tool": tool_name,
                        "arguments": json.loads(row.arguments),
                    }
                ),
                "server": row.server_name,
                "tool": row.tool_name,
                "arguments": json.loads(row.arguments),
                "retrieved_at": row.retrieved_at,
                "status": "ok",
                "partial": False,
                "warnings": ["served_from_cache"],
                "data": json.loads(row.response),
                "meta": {"cache_hit": True, "hit_count": row.hit_count},
            }

    def put(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        response: dict[str, Any],
        ttl_seconds: int | None = None,
    ) -> str:
        """Persist an evidence envelope to mcp_cache. Returns args_hash."""
        args_hash = canonical_args_hash(arguments)
        retrieved_at = datetime.now(UTC).isoformat()
        if ttl_seconds is None:
            ttl_seconds = self._ttl_for(tool_name)
        expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat()
        with self._session_ctx() as s:
            MCPCache = _MCPCache()
            row = (
                s.query(MCPCache)
                .filter_by(server_name=server_name, tool_name=tool_name, args_hash=args_hash)
                .first()
            )
            if row is None:
                row = MCPCache(
                    server_name=server_name,
                    tool_name=tool_name,
                    args_hash=args_hash,
                    arguments=json.dumps(arguments, sort_keys=True, default=str),
                    response=json.dumps(response, sort_keys=True, default=str),
                    retrieved_at=retrieved_at,
                    expires_at=expires_at,
                    hit_count=0,
                )
                s.add(row)
            else:
                row.response = json.dumps(response, sort_keys=True, default=str)
                row.retrieved_at = retrieved_at
                row.expires_at = expires_at
                row.hit_count = 0
            s.commit()
        return args_hash

    def get_stale(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return the most recent cached entry regardless of expiry (for degradation)."""
        args_hash = canonical_args_hash(arguments)
        with self._session_ctx() as s:
            MCPCache = _MCPCache()
            row = (
                s.query(MCPCache)
                .filter_by(server_name=server_name, tool_name=tool_name, args_hash=args_hash)
                .order_by(MCPCache.retrieved_at.desc())
                .first()
            )
            if row is None:
                return None
            return {
                "evidence_id": envelope_key(
                    {
                        "server": server_name,
                        "tool": tool_name,
                        "arguments": json.loads(row.arguments),
                    }
                ),
                "server": row.server_name,
                "tool": row.tool_name,
                "arguments": json.loads(row.arguments),
                "retrieved_at": row.retrieved_at,
                "status": "partial",
                "partial": True,
                "warnings": ["served_from_stale_cache", "cache_expired"],
                "data": json.loads(row.response),
                "meta": {"cache_hit": True, "stale": True, "hit_count": row.hit_count},
            }