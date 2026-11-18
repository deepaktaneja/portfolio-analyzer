"""Validation & sanitisation of external MCP responses.

Protects against oversized payloads and prompt-injection text embedded in
third-party content. Sanitisation only affects how content is presented to an
LLM; stored accounting data is never altered.
"""
from __future__ import annotations

import json
import re

DEFAULT_MAX_BYTES = 2_000_000

_INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"ignore\s+all\s+previous",
    r"system\s*:",
    r"you\s+are\s+now",
    r"disregard\s+",
    r"\n\nHuman:",
    r"\n\nAssistant:",
    r"</?system>",
]


def response_size(data) -> int:
    try:
        return len(json.dumps(data, default=str))
    except (TypeError, ValueError):
        return len(str(data))


def validate_response(data, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[bool, list[str]]:
    """Return (within_limits, warnings) for an MCP response payload."""
    warnings: list[str] = []
    if data is None:
        warnings.append("empty_response")
        return True, warnings
    if not isinstance(data, (dict, list)):
        warnings.append("non_structured_response")
    size = response_size(data)
    if size > max_bytes:
        warnings.append(f"oversized_response:{size}> {max_bytes}")
        return False, warnings
    return True, warnings


def sanitise_external_text(text: str | None) -> str:
    """Neutralise common prompt-injection phrases in external text."""
    if not text:
        return ""
    cleaned = str(text)
    for pat in _INJECTION_PATTERNS:
        cleaned = re.sub(pat, " [redacted] ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def find_injection(text: str | None) -> list[str]:
    """Return the injection patterns detected in ``text`` (for auditing)."""
    if not text:
        return []
    return [pat for pat in _INJECTION_PATTERNS if re.search(pat, str(text), flags=re.IGNORECASE)]
