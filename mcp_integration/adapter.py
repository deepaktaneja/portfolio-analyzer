"""MCP adapter: validates discovered tools, calls tools, builds evidence envelopes.

Never assumes tool names — always discovers via list_tools. Calling an
undiscovered tool raises ValueError. Call arguments are validated against the
discovered inputSchema. Every result is wrapped in an evidence envelope and
persisted to mcp_cache.
"""
from __future__ import annotations

import time
from typing import Any

import jsonschema

from mcp_integration.cache import MCPCacheStore
from mcp_integration.client import MCPClientManager
from mcp_integration.evidence import build_envelope, envelope_key


class MCPAdapter:
    """High-level MCP client with schema validation, caching, and circuit breaking."""

    def __init__(self, manager: MCPClientManager | None = None, cache: MCPCacheStore | None = None):
        self.manager = manager or MCPClientManager()
        self.cache = cache or MCPCacheStore()

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Call an MCP tool with full validation, caching, and evidence."""
        # 1. Server must exist and be enabled
        if not self.manager.is_enabled(server):
            return self._degraded(
                server, tool, arguments,
                warning=f"server '{server}' not in trusted registry or disabled",
            )

        # 2. Connect and discover tools
        try:
            async with self.manager.connect(server) as conn:
                # 3. Tool must be discovered
                if tool not in conn.tools:
                    raise ValueError(
                        f"Tool '{tool}' not discovered on server '{server}'. "
                        f"Discovered tools: {sorted(conn.tools.keys())}"
                    )
                tool_info = conn.tools[tool]

                # 4. Validate arguments against discovered inputSchema
                self._validate_arguments(tool_info.input_schema, arguments)

                # 5. Cache check
                if use_cache:
                    cached = self.cache.get(server, tool, arguments)
                    if cached is not None:
                        return cached

                # 6. Execute call under concurrency semaphore
                async with conn.semaphore:
                    start = time.monotonic()
                    try:
                        result = await conn.session.call_tool(tool, arguments)
                        elapsed = time.monotonic() - start
                        self.manager._record_success(conn)
                    except Exception as exc:  # noqa: BLE001
                        self.manager._record_failure(conn)
                        stale = self.cache.get_stale(server, tool, arguments)
                        if stale is not None:
                            return stale
                        return self._degraded(
                            server, tool, arguments,
                            warning=f"MCP call failed: {exc}",
                            error=True,
                        )

                # 7. Normalise result data
                data = self._normalise_result(result)

                # 7b. Validate external response (size guard / structure / injection audit)
                from mcp_integration.validation import validate_response
                within_limits, vwarn = validate_response(data)

                # 8. Build and persist evidence envelope
                envelope = build_envelope(
                    server=server,
                    tool=tool,
                    arguments=arguments,
                    data=data,
                    status="ok",
                    partial=not within_limits,
                    warnings=list(vwarn),
                    meta={"elapsed_seconds": round(elapsed, 4)},
                )
                if within_limits:
                    self.cache.put(server, tool, arguments, data)
                envelope["evidence_id"] = envelope_key(envelope)
                return envelope

        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            stale = self.cache.get_stale(server, tool, arguments)
            if stale is not None:
                return stale
            return self._degraded(
                server, tool, arguments,
                warning=f"MCP connection error: {exc}",
                error=True,
            )

    def _validate_arguments(self, input_schema: dict[str, Any], arguments: dict[str, Any]) -> None:
        """Validate call arguments against the discovered JSON Schema inputSchema."""
        if not input_schema:
            return
        try:
            jsonschema.validate(instance=arguments, schema=input_schema)
        except jsonschema.ValidationError as exc:
            raise ValueError(
                f"Arguments failed inputSchema validation: {exc.message}"
            ) from exc

    def _normalise_result(self, result: Any) -> dict[str, Any]:
        """Normalise an MCP CallToolResult into a plain dict."""
        if hasattr(result, "model_dump"):
            return result.model_dump()
        if hasattr(result, "dict"):
            return result.dict()
        if isinstance(result, dict):
            return dict(result)
        content = getattr(result, "content", None)
        if content is not None:
            blocks = []
            for block in content:
                if hasattr(block, "model_dump"):
                    blocks.append(block.model_dump())
                elif hasattr(block, "dict"):
                    blocks.append(block.dict())
                else:
                    blocks.append(str(block))
            return {"content": blocks}
        return {"data": str(result)}

    def _degraded(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        warning: str,
        error: bool = False,
    ) -> dict[str, Any]:
        """Build a partial evidence envelope when the MCP call cannot complete."""
        envelope = build_envelope(
            server=server,
            tool=tool,
            arguments=arguments,
            data=None,
            status="error" if error else "partial",
            partial=True,
            warnings=[warning],
            meta={"degraded": True},
        )
        envelope["evidence_id"] = envelope_key(envelope)
        return envelope

    async def discover_and_validate(self, server: str) -> list[str]:
        """Connect to a server and return its discovered tool names."""
        if not self.manager.is_enabled(server):
            raise ValueError(f"Server '{server}' not in trusted registry or disabled")
        async with self.manager.connect(server) as conn:
            return sorted(conn.tools.keys())