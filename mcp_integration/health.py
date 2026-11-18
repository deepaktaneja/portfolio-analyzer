"""MCP health checks and retry helpers for reliable integrations.

Health checks probe a server's connection + tool discovery with a timeout and
report latency/status. Retries use exponential backoff with jitter. Nothing here
mutates portfolio data — failures are reported, not propagated into accounting.
"""
from __future__ import annotations

import asyncio
import random
import time


async def retry_async(factory, retries: int = 2, base_delay: float = 0.5,
                      max_delay: float = 8.0, jitter: float = 0.2):
    """Await ``factory()`` with exponential backoff. Re-raises the last error."""
    last_exc = None
    for attempt in range(int(retries) + 1):
        try:
            return await factory()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= retries:
                break
            delay = min(max_delay, base_delay * (2 ** attempt))
            delay += random.uniform(0, jitter * delay)
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


async def health_check(manager, server: str, timeout_seconds: float | None = None) -> dict:
    """Probe one MCP server (connect + list tools). Returns a status dict."""
    cfg = manager.server_config(server) if hasattr(manager, "server_config") else None
    if cfg is not None and not getattr(cfg, "enabled", True):
        return {"server": server, "status": "disabled", "latency_ms": None}
    timeout = timeout_seconds or (getattr(cfg, "timeout_seconds", 30) if cfg else 30)
    start = time.monotonic()

    async def _probe():
        async with manager.connect(server) as conn:
            return {"server": server, "status": "ok", "tools": len(conn.tools),
                    "tool_names": sorted(conn.tools.keys())[:25]}

    try:
        result = await asyncio.wait_for(_probe(), timeout=timeout)
        result["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        return result
    except Exception as exc:  # noqa: BLE001
        return {"server": server, "status": "error", "error": str(exc),
                "latency_ms": round((time.monotonic() - start) * 1000, 1)}


async def health_check_all(manager, servers: list[str] | None = None,
                           timeout_seconds: float | None = None) -> list[dict]:
    """Probe all enabled servers concurrently."""
    names = servers
    if names is None:
        names = [s.name for s in getattr(manager, "registry", []) if getattr(s, "enabled", True)]
    return list(await asyncio.gather(*[health_check(manager, n, timeout_seconds) for n in names]))


def run_health_checks(manager, servers: list[str] | None = None,
                      timeout_seconds: float | None = None) -> list[dict]:
    """Synchronous wrapper for health_check_all (safe to call from Streamlit/CLI)."""
    try:
        return asyncio.run(health_check_all(manager, servers, timeout_seconds))
    except RuntimeError:
        # Already inside an event loop — fall back to a fresh loop in a thread-safe way.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(health_check_all(manager, servers, timeout_seconds))
        finally:
            loop.close()
