"""Redaction helpers to prevent secret/PII leakage in logs and LLM prompts."""
from __future__ import annotations

import logging
import re

_REDACTED = "[REDACTED]"

_PATTERNS = [
    # key/value style secrets
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization)\b\s*[:=]\s*\S+"),
     r"\1: " + _REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), "Bearer " + _REDACTED),
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), _REDACTED),            # OpenAI-style
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), _REDACTED),         # Google API keys
    (re.compile(r"gsk_[A-Za-z0-9]{16,}"), _REDACTED),            # Groq-style
    # emails (mask local part, keep domain)
    (re.compile(r"(?i)\b([A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]*@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"),
     r"***@\2"),
    # long opaque hex/base64 blobs (keys/tokens)
    (re.compile(r"\b[A-Fa-f0-9]{40,}\b"), _REDACTED),
]


def redact_text(text: str | None) -> str:
    """Return text with secrets and emails masked."""
    if not text:
        return text or ""
    out = str(text)
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact_secret(value: str | None, keep_last: int = 4) -> str:
    """Mask a secret value, optionally preserving the final characters."""
    if not value:
        return ""
    v = str(value)
    if len(v) <= keep_last:
        return "*" * len(v)
    return "*" * (len(v) - keep_last) + v[-keep_last:]


class RedactionFilter(logging.Filter):
    """Logging filter that scrubs secrets/PII from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_text(record.getMessage())
            record.args = None
        except Exception:  # noqa: BLE001, S110
            pass
        return True
