# Configuration Reference — Stage 10

All configuration is local and file/env based. No cloud configuration service is used.

## Environment (`.env`)

See `.env.example`. Groups: database paths (`ICICI_*_DB_PATH`), LLM keys, automation/security.

## Market data price fallback (Yahoo Finance)

The local `market_data.sqlite3` only holds the imported price history (today: 2020-01-01 →
2024-12-30), so XIRR for periods outside that window needs an on-demand source.

| Variable | Default | Meaning |
| --- | --- | --- |
| `ICICI_PRICE_FETCH_FALLBACK` | `true` | Allow a Yahoo Finance fetch when the DB has no close for the requested date. |
| `ICICI_PRICE_FETCH_PERSIST` | `true` | Write fetched rows into `stock_prices` so later runs are offline. |
| `ICICI_PRICE_FETCH_MAX_SYMBOLS` | `10` | Per price-lookup (page render) fetch budget; `0` = unlimited. |
| `ICICI_PRICE_PROVIDER` | `auto` | `auto` (yfinance → Yahoo chart HTTP), `yfinance`, or `yahoo_chart`. |

Two price sources are available: the `yfinance` library (`YFinanceProvider`) and a
dependency-free JSON endpoint (`YahooChartProvider`, `query1.finance.yahoo.com/v8/finance/chart`).
With `auto`, a failed/empty yfinance call automatically falls through to the HTTP endpoint;
Data & Settings → Fetch Historical Prices also exposes an explicit provider picker.

Lookup order in `app.shared.price_lookup_factory`: local DB → imported holdings snapshot
(only when the end date is within 45 days of the snapshot) → on-demand fetch (cached
in-process). Fetching needs a symbol mapping per security (`security_symbol_map`, e.g.
`RELIANCE.NS`); use **Data & Settings → Symbol Mapping** for missing entries.

To pre-populate a whole period (recommended before running long historical XIRR windows),
use **Data & Settings → Fetch Historical Prices (Yahoo Finance)**. That path is not limited
by `ICICI_PRICE_FETCH_MAX_SYMBOLS` and reports per-security row counts.

## Schedule — `config/scheduler.yaml`

```yaml
jobs:
  refresh_prices:            { enabled: true, interval_hours: 24, scope: daily }
  refresh_corporate_actions: { enabled: true, interval_hours: 24, scope: daily }
  refresh_research:          { enabled: true, interval_hours: 720, scope: monthly }
  evaluate_alerts:           { enabled: true, interval_hours: 6,  scope: hourly }
  backup_databases:          { enabled: true, interval_hours: 24, scope: daily }
```

- `interval_hours` — cadence.
- `scope` — idempotency granularity (`hourly|daily|weekly|monthly|quarterly`). A job never
  double-processes the same period key, even across restarts or repeated triggers.

## Alerts — `config/alerts.yaml`

Per-type channels, cooldown and frequency. `in_app` is safe; `email`/`webhook` are **stubs** that only
log (no external send) until explicitly approved.

```yaml
default: { enabled: true, channels: [in_app], cooldown_minutes: 360, frequency: immediate, min_severity: INFO }
types:
  SIGNAL_EXIT: { cooldown_minutes: 60, min_severity: INFO }
  SIGNAL_MONITOR: { enabled: false }   # reduce noise
```

## Allocation & rebalancing — `config/allocation_targets.yaml`

Stage 9 targets, sector caps, market-cap bands, rebalancing strategy and tax overrides.

## Decision thresholds — `config/decision_thresholds.yaml`

Stage 8 signal thresholds (concentration, valuation, appreciation, earnings, governance, MF).

## MCP registry — `config/mcp_servers.json`

Trusted MCP servers only (stdio/streamable_http). Undiscovered-tool calls raise `ValueError`.

## Auth — `config/auth.yaml` + env

Auth is disabled unless `APP_AUTH_ENABLED=true`. Roles: `admin`, `editor`, `viewer`.
Passwords: plaintext (dev) or `sha256:<hex>`. Prefer env `APP_USERNAME` / `APP_PASSWORD_SHA256`.

## Logging

- `LOG_FORMAT=json` → structured JSON lines; default is human-readable.
- `LOG_LEVEL=INFO|DEBUG|WARNING|ERROR`.
- Secrets/PII (API keys, bearer tokens, emails) are automatically redacted.

## Backups

`BACKUP_ENCRYPTION_KEY` enables the `backup_databases` job and `scripts.backup`. Accepts a Fernet
key or a passphrase (PBKDF2-derived). Keep this key safe — backups cannot be restored without it.
