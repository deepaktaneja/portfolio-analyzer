# Release Checklist

Verified with real ICICI Direct files:
- PDF transaction import (text-based)
- XLS/TSV holdings import

Validated with synthetic fixtures only:
- Corporate actions
- Dividends
- FIFO gains
- XIRR/TWR
- Benchmarks
- Reports

Not production-ready: material reconciliation and calculation edge cases remain.

## Stage 10 — Automation, Reliability & Production Readiness

Verified:
- Scheduled jobs idempotent & recoverable (per-period keys; stale-run recovery)
- Failed MCP calls degrade to stale/partial; portfolio data unchanged
- Alerts accurate, configurable (`config/alerts.yaml`), de-duplicated
- Encrypted backup → verified restore (sha256 + `PRAGMA integrity_check`)
- Structured logging with secret/PII redaction
- Optional auth + least-privilege roles (disabled by default)
- Docker image + compose provided (build/deploy NOT executed pending approval)
- Full test suite green (310 passed)

Not executed (requires approval):
- `docker compose up --build` / production deployment
- External notifications (email/webhook remain logging stubs)

