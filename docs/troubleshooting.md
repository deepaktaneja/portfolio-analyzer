# Troubleshooting

- Missing sample PDF: tests skip if file absent.
- Import errors: check folder paths and file types.
- Reconciliation mismatches: verify holdings file and transaction coverage.
- Undefined XIRR: ensure both inflows and outflows exist.

## Stage 10 — Automation, Alerts, Security & Monitoring

### Scheduled jobs
- **"not_due"** — the job already completed for the current idempotency key
  (`hourly|daily|weekly|monthly|quarterly`). Inspect with `python -m scheduler --list`.
  This is intended de-duplication; wait for the next key or change `scope` in `config/scheduler.yaml`.
- **Stuck "running"** — interrupted run; the runner recovers stale runs after
  `stale_run_timeout_minutes` (default 30) and retries failed keys. Manual: `python -m scheduler --recover`.
- **`refresh_research` skipped** — no LLM/MCP configured; research is never fabricated.
- **`backup_databases` skipped** — set `BACKUP_ENCRYPTION_KEY` in `.env`.

### Alerts
- No alerts → click **Scan for new alerts** (Portfolio page) or `python -m scheduler --job evaluate_alerts`.
- Duplicates are suppressed within the cooldown window (`config/alerts.yaml`); `suppressed_count` increments.
- Noisy type → set `enabled: false` or raise `min_severity` for that type.
- Email/webhook never send — they are logging stubs pending explicit approval.

### MCP reliability
- **Circuit open** — after N consecutive failures the server is skipped during cooldown; stale cache is
  served when available, else a partial envelope is returned. Failed calls never modify portfolio data.
- **Oversized/injected responses** — flagged `partial` and not cached; injection phrases neutralised before LLM use.

### Backups & recovery
- `python -m scripts.backup` / `python -m scripts.restore <file>.db.enc <target>` (verifies sha256 +
  `PRAGMA integrity_check` before replacing).
- `decryption failed: wrong key or corrupt data` — the key differs from creation time or the file is corrupt.

### Logging & privacy
- `LOG_FORMAT=json` for structured logs; secrets/emails are redacted automatically; LLM prompt/response
  text is not stored (usage records token counts + estimated cost only).

### Authentication
- Disabled by default. If `APP_AUTH_ENABLED=true` and locked out, unset it or reset
  `APP_USERNAME` / `APP_PASSWORD_SHA256`. Roles: `admin`, `editor`, `viewer`.

See `docs/DEPLOYMENT.md`, `docs/CONFIGURATION.md` for full procedures.
