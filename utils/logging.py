"""Structured logging with optional JSON output and secret/PII redaction.

Backward compatible: ``get_logger(name)`` still returns a configured logger.
Set ``LOG_FORMAT=json`` for JSON lines and ``LOG_LEVEL`` to control verbosity.
A redaction filter is attached so API keys/emails never reach the log sinks.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from security.redaction import RedactionFilter

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    """Minimal structured JSON log formatter (one object per line)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _build_formatter() -> logging.Formatter:
    if os.getenv("LOG_FORMAT", "").lower() == "json":
        return JsonFormatter()
    return logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")


def get_logger(name: str = "portfolio_analyzer") -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger(name)
    if not _CONFIGURED:
        _CONFIGURED = True
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        logger.setLevel(getattr(logging, level, logging.INFO))
        formatter = _build_formatter()
        redactor = RedactionFilter()
        try:
            os.makedirs("logs", exist_ok=True)
            handler = RotatingFileHandler("logs/app.log", maxBytes=1_000_000, backupCount=3)
            handler.setFormatter(formatter)
            handler.addFilter(redactor)
            logger.addHandler(handler)
        except OSError:
            pass  # read-only FS (e.g. container) — console logging still works
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.addFilter(redactor)
        logger.addHandler(console)
    return logger

