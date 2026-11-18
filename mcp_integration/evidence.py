"""Evidence envelope builder for MCP calls.

Every MCP result is wrapped in a deterministic evidence envelope and
persisted to mcp_cache. research_reports.evidence_ids cites envelope
IDs so LLM research is traceable to source data.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any


def canonical_args_hash(arguments: dict[str, Any]) -> str:
    """SHA-256 of canonicalised (sorted-key JSON) arguments for idempotent dedup."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_envelope(
    *,
    server: str,
    tool: str,
    arguments: dict[str, Any],
    data: Any,
    status: str = "ok",
    retrieved_at: str | None = None,
    partial: bool = False,
    warnings: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic evidence envelope for an MCP call result."""
    if retrieved_at is None:
        retrieved_at = datetime.now(UTC).isoformat()
    return {
        "evidence_id": uuid.uuid4().hex,
        "server": server,
        "tool": tool,
        "arguments": arguments,
        "retrieved_at": retrieved_at,
        "status": status,
        "partial": partial,
        "warnings": warnings or [],
        "data": data,
        "meta": meta or {},
    }


def envelope_key(envelope: dict[str, Any]) -> str:
    """Composite cache key: server|tool|args_hash."""
    return f"{envelope['server']}|{envelope['tool']}|{canonical_args_hash(envelope['arguments'])}"