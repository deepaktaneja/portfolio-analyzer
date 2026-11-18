"""Copilot security: input sanitisation, tool-arg validation, rate limiting, audit.

All copilot tools are READ-ONLY (no trades). The guard defends against prompt
injection, oversized input, invalid tool arguments and excessive call volume.
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?(previous|prior)",
    r"you\s+are\s+now",
    r"\bsystem\s*:",
    r"\bdeveloper\s*:",
    r"</?system>",
    r"\n\nHuman:",
    r"\n\nAssistant:",
    r"reveal\s+(your\s+)?(system\s+)?prompt",
    r"print\s+(your\s+)?(api|secret|key|password)",
]


def sanitise_user_input(text: str, max_chars: int = 2000) -> str:
    """Sanitise untrusted user text before it enters any prompt or tool args."""
    if not text:
        return ""
    cleaned = str(text)[:max_chars]
    for pat in _INJECTION_PATTERNS:
        cleaned = re.sub(pat, " [redacted] ", cleaned, flags=re.IGNORECASE)
    # reuse the research sanitiser for consistency, if available
    try:
        from research.llm import sanitise_mcp_content
        cleaned = sanitise_mcp_content(cleaned)
    except Exception:  # noqa: BLE001
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.strip()


def detect_injection(text: str) -> list[str]:
    """Return the injection patterns detected in ``text`` (for auditing)."""
    if not text:
        return []
    return [p for p in _INJECTION_PATTERNS if re.search(p, str(text), re.IGNORECASE)]


@dataclass
class ValidationResult:
    ok: bool
    args: dict = field(default_factory=dict)
    error: str | None = None


def validate_args(spec, args: dict | None) -> ValidationResult:
    """Validate/coerce tool arguments against the tool's simple args_schema.

    ``spec.args_schema`` maps arg name -> {"type": float|int|str, "required": bool,
    "min": number, "max": number}. Unknown args are dropped (least privilege).
    """
    args = dict(args or {})
    schema = getattr(spec, "args_schema", {}) or {}
    cleaned: dict = {}
    for name, rule in schema.items():
        if name not in args or args[name] is None:
            if rule.get("required"):
                return ValidationResult(False, {}, f"missing required argument '{name}'")
            continue
        val = args[name]
        typ = rule.get("type")
        try:
            if typ == "float":
                val = float(val)
            elif typ == "int":
                val = int(float(val))
            elif typ == "str":
                val = str(val)
        except (TypeError, ValueError):
            return ValidationResult(False, {}, f"argument '{name}' has invalid type")
        if "min" in rule and isinstance(val, (int, float)) and val < rule["min"]:
            return ValidationResult(False, {}, f"argument '{name}' below minimum {rule['min']}")
        if "max" in rule and isinstance(val, (int, float)) and val > rule["max"]:
            return ValidationResult(False, {}, f"argument '{name}' above maximum {rule['max']}")
        cleaned[name] = val
    return ValidationResult(True, cleaned)


def authorize(role: str, tool_name: str) -> bool:
    """All copilot tools are read-only and require 'view' permission."""
    try:
        from app.auth import can
        return can(role or "viewer", "view")
    except Exception:  # noqa: BLE001 — auth disabled => allow (local single-user)
        return True


class RateLimiter:
    """Sliding-window per-session rate limiter (calls per minute)."""

    def __init__(self, per_minute: int = 30):
        self.per_minute = max(1, int(per_minute))
        self._events: dict[str, deque] = {}

    def allow(self, session_id: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self._events.setdefault(session_id, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


class AuditLogger:
    """Bounded in-memory audit trail + redacted structured logs (no PII in logs)."""

    def __init__(self, max_entries: int = 200):
        self.max_entries = max_entries
        self.entries: list[dict] = []

    def record(self, tool: str, args: dict, ok: bool, detail: str = "", session_id: str | None = None) -> None:
        entry = {
            "ts": time.time(),
            "tool": tool,
            "args": _redact_args(args),
            "ok": bool(ok),
            "detail": detail,
            "session": (session_id or "")[:8],
        }
        self.entries.append(entry)
        if len(self.entries) > self.max_entries:
            self.entries.pop(0)
        try:
            from utils.logging import get_logger
            get_logger("copilot.audit").info("copilot tool=%s ok=%s detail=%s", tool, ok, detail)
        except Exception:  # noqa: BLE001, S110
            pass


def _redact_args(args: dict) -> dict:
    """Keep numeric keys; mask free-text (may contain identifiers) in logs."""
    out = {}
    for k, v in (args or {}).items():
        out[k] = v if isinstance(v, (int, float, bool)) else "***"
    return out
