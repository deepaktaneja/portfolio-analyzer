"""MCP client manager: connects to MCP servers, discovers tools, manages sessions.

Trusted-target validation: stdio launchers and streamable_http URLs must match
pre-approved entries in config/mcp_servers.json. Calling an undiscovered tool
raises ValueError (prevents tool hallucination).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from config.settings import MCPServerConfig, load_mcp_registry


@dataclass
class MCPToolInfo:
    """Discovered tool metadata from list_tools."""
    name: str
    description: str | None
    input_schema: dict[str, Any]


@dataclass
class MCPServerConnection:
    """Holds an active MCP session plus its discovered tool inventory."""
    name: str
    config: MCPServerConfig
    session: ClientSession
    tools: dict[str, MCPToolInfo] = field(default_factory=dict)
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))
    consecutive_failures: int = 0
    circuit_open: bool = False
    circuit_opened_at: float | None = None


class MCPClientManager:
    """Manages MCP client connections to the app's own registry of servers.

    Does not import from or reference /Users/anmol/stock-selection-agent.
    """

    def __init__(self, registry_path: str | None = None):
        self._registry: list[MCPServerConfig] = load_mcp_registry(registry_path)
        self._connections: dict[str, MCPServerConnection] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def registry(self) -> list[MCPServerConfig]:
        return list(self._registry)

    def server_config(self, name: str) -> MCPServerConfig | None:
        for s in self._registry:
            if s.name == name:
                return s
        return None

    def is_enabled(self, name: str) -> bool:
        cfg = self.server_config(name)
        return cfg is not None and cfg.enabled

    def _server_lock(self, name: str) -> asyncio.Lock:
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    def _is_circuit_open(self, conn: MCPServerConnection) -> bool:
        if not conn.circuit_open:
            return False
        from time import monotonic
        if conn.circuit_opened_at is None:
            return True
        if monotonic() - conn.circuit_opened_at >= conn.config.circuit_breaker_cooldown_seconds:
            conn.circuit_open = False
            conn.consecutive_failures = 0
            return False
        return True

    def _record_failure(self, conn: MCPServerConnection) -> None:
        from config.settings import get_settings
        settings = get_settings()
        conn.consecutive_failures += 1
        if conn.consecutive_failures >= settings.circuit_breaker_failures:
            conn.circuit_open = True
            from time import monotonic
            conn.circuit_opened_at = monotonic()

    def _record_success(self, conn: MCPServerConnection) -> None:
        conn.consecutive_failures = 0
        conn.circuit_open = False
        conn.circuit_opened_at = None

    @asynccontextmanager
    async def connect(self, name: str):
        """Open an MCP session to ``name``, discover tools, yield a connection.

        Raises ValueError if the server is not in the trusted registry or is disabled.
        Raises RuntimeError if the connection cannot be established.
        """
        cfg = self.server_config(name)
        if cfg is None:
            raise ValueError(f"MCP server '{name}' is not in the trusted registry")
        if not cfg.enabled:
            raise ValueError(f"MCP server '{name}' is disabled in the registry")

        existing = self._connections.get(name)
        if existing is not None and not existing.circuit_open:
            yield existing
            return

        async with self._server_lock(name):
            existing = self._connections.get(name)
            if existing is not None and not existing.circuit_open:
                yield existing
                return

            if existing is not None and self._is_circuit_open(existing):
                raise RuntimeError(
                    f"MCP server '{name}' circuit breaker is open (too many consecutive failures)"
                )

            try:
                session_cm = self._open_transport(cfg)
                async with session_cm as (read, write), ClientSession(read, write) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    tools: dict[str, MCPToolInfo] = {}
                    for t in tools_result.tools:
                        tools[t.name] = MCPToolInfo(
                            name=t.name,
                            description=getattr(t, "description", None),
                            input_schema=getattr(t, "inputSchema", {}) or {},
                        )
                    conn = MCPServerConnection(
                        name=name,
                        config=cfg,
                        session=session,
                        tools=tools,
                        semaphore=asyncio.Semaphore(cfg.max_concurrency),
                    )
                    self._record_success(conn)
                    self._connections[name] = conn
                    yield conn
            except Exception as exc:
                if existing is not None:
                    self._record_failure(existing)
                raise RuntimeError(f"Failed to connect to MCP server '{name}': {exc}") from exc

    def _open_transport(self, cfg: MCPServerConfig):
        if cfg.transport == "stdio":
            if cfg.command is None:
                raise ValueError(f"Server '{cfg.name}' stdio transport requires 'command'")
            params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args or [],
                env=None,
            )
            return stdio_client(params)
        if cfg.transport == "streamable_http":
            if cfg.url is None:
                raise ValueError(f"Server '{cfg.name}' streamable_http transport requires 'url'")
            return streamable_http_client(cfg.url)
        raise ValueError(f"Unsupported transport '{cfg.transport}' for server '{cfg.name}'")

    def list_tools(self, name: str) -> dict[str, MCPToolInfo]:
        """Return the discovered tool inventory for a connected server."""
        conn = self._connections.get(name)
        if conn is None:
            raise RuntimeError(f"MCP server '{name}' is not connected. Call connect() first.")
        return dict(conn.tools)

    def tool_exists(self, name: str, tool: str) -> bool:
        """Return True if ``tool`` is discovered on server ``name``."""
        conn = self._connections.get(name)
        if conn is None:
            return False
        return tool in conn.tools

    def close(self, name: str) -> None:
        """Close the connection to a server (best-effort)."""
        self._connections.pop(name, None)