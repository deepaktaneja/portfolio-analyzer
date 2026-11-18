# Deployment Guide — Stage 10

Reproducible local/private deployment of the Investment Portfolio Intelligence Platform.
**Advisory only — no autonomous trading.** External notifications remain disabled by default.

## 1. Prerequisites

- Python 3.12+ **or** Docker 24+ (for container deployment)
- ~500 MB disk for SQLite DBs + backups
- Network access only for MCP servers / LLM providers (optional; the app runs offline with manual data)

## 2. Local (virtualenv) deployment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # fill secrets
python -m scripts.migrate # alembic upgrade head + provision standalone DBs
streamlit run app.py
```

Run the scheduler in a second terminal (or schedule via cron):

```bash
python -m scheduler --loop 300      # run due jobs every 5 minutes
python -m scheduler --list          # show job registry & last status
python -m scheduler --job refresh_prices
```

## 3. Docker deployment

```bash
cp .env.example .env       # fill secrets (BACKUP_ENCRYPTION_KEY, LLM keys, ...)
docker compose up --build  # starts app (:8501) + scheduler worker
```

- The `app` service runs migrations on startup via `docker/entrypoint.sh`.
- The `scheduler` service runs `python -m scheduler --loop 300`.
- Data, config and logs are bind-mounted from the host (`./data`, `./config`, `./logs`).
- A container `HEALTHCHECK` probes `/_stcore/health`.

Open http://localhost:8501.

## 4. Environment variables

See `.env.example` and `docs/CONFIGURATION.md`. Key values:

| Variable | Purpose |
|---|---|
| `BACKUP_ENCRYPTION_KEY` | Fernet key **or** passphrase for encrypted backups |
| `APP_AUTH_ENABLED` / `APP_USERNAME` / `APP_PASSWORD` | Optional login gate |
| `LOG_FORMAT=json` | Structured JSON logs |
| `ICICI_*_DB_PATH` | Per-database SQLite locations |

## 5. Database migration

```bash
python -m scripts.migrate          # upgrade to head + provision standalone DBs
python -m scripts.migrate 0004_research_tables   # specific revision
```

Alembic manages the core schema (`alembic/`); research/decision/MF/scheduler/alerts/usage
databases are self-provisioning (created idempotently on first use).

## 6. Backup & restore

```bash
# Encrypted backups of all databases
BACKUP_ENCRYPTION_KEY=... python -m scripts.backup --dest-dir data/backups

# Verify + restore (integrity_check before overwrite)
BACKUP_ENCRYPTION_KEY=... python -m scripts.restore \
  data/backups/xirr.sqlite3.<ts>.db.enc data/xirr.sqlite3
```

Backups are Fernet-encrypted with a `.sha256` sidecar. Restore **verifies** the hash and runs
`PRAGMA integrity_check` before atomically replacing the target — a corrupt backup never overwrites
good data. The `backup_databases` scheduled job performs this automatically when a key is configured.

## 7. Monitoring

- Data & Settings → **Automation, Monitoring & Backups** expander: scheduler status, run-due button,
  MCP health checks, metrics snapshot + Prometheus export, and encrypted backup button.
- Metrics include scheduler runs, alert counts, LLM token/cost usage, MCP cache stats and DB sizes.

## 8. Authentication & roles (optional)

Set `APP_AUTH_ENABLED=true` and credentials via env (prefer `APP_PASSWORD_SHA256`). Roles:
`admin` (all), `editor` (run jobs, acknowledge alerts), `viewer` (read-only). Disabled by default so
local/test usage is unaffected.

## 9. Release checklist

- [ ] `python -m pytest -q` green
- [ ] `ruff check .` clean
- [ ] `python -m scripts.migrate` succeeds on a fresh `data/` dir
- [ ] Backup created **and restore verified** on a copy
- [ ] `docker compose up --build` → healthcheck healthy
- [ ] Confirm no external notifications are enabled
