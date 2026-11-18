# Architecture — Indian Investment Portfolio Intelligence Application

**Stage 0 deliverable.** Architecture, technology stack, and module boundaries for a standalone Indian portfolio intelligence application supporting direct equities and mutual funds. This document is a specification only — no application code is implemented during Stage 0.

## 1. Design principles

| Principle | Rationale |
|---|---|
| Standalone & independent | Own codebase, database, UI, and LLM workflows in a **separate directory/venv**. No imports from, and no modifications to, the existing Indian Equity Research Application (`/Users/anmol/stock-selection-agent`). |
| Reuse MCP servers directly | Connect to existing MCP servers (company-research, bulk-screener, nse-history, nse-market) and to specified MF servers (mftool-mcp primary, TigZig Indian MF MCP fallback) via the official `mcp` Python SDK. The new app maintains its own registry config. |
| Simple modular monolith | Single deployable Python package + Streamlit UI + SQLite. No microservices, no message queue, no multi-agent framework. |
| Local-first | SQLite file on disk; network used only for MCP servers, market data, and LLM inference. |
| Deterministic & auditable accounting | All financial calculations (XIRR, FIFO lots, corporate actions, gains) are pure Python with deterministic tests and full audit trails. LLMs provide research and interpretation only — never accounting. |
| No autonomous trading | Review signals (HOLD/MONITOR/TRIM/EXIT) are advisory only; no order placement or execution. |

## 2. Technology stack

| Layer | Choice | Version note |
|---|---|---|
| Language | Python | 3.12+ |
| UI | Streamlit | Reactive single-page app |
| Database | SQLite | One file per portfolio/user |
| ORM / migrations | SQLAlchemy 2.0 + Alembic | Declarative models, versioned schema |
| Data manipulation | pandas, numpy | Vectorised holdings/valuation math |
| Financial math | `pyxirr`, scipy | XIRR root-finding (fixed tolerance/cap) |
| Validation | pydantic v2 | Input schemas and config |
| MCP client | official `mcp` SDK | stdio + streamable_http transports |
| LLM | Gemini → OpenRouter → local LLM (quota-based fallback chain) | Orchestrator/research only, low temperature; fall back to next provider when free quota exhausted |
| Config / secrets | python-dotenv + YAML/JSON registry | `.env` for API keys, registry for MCP servers |
| Testing / linting | pytest, ruff | Unit + integration + golden tests |

The `mcp` SDK is **not** currently installed in the portfolio-analyzer venv; it must be added to `requirements.txt` in Stage 1.

## 3. Module boundaries

Layers depend downward only. No upward or circular imports.

```
portfolio_intelligence/
├── config/           # settings, MCP registry, logging, env loading
├── core/             # domain models, enums, constants, money/date helpers
├── db/               # SQLAlchemy models, Alembic migrations, repository layer
├── importing/        # statement parsers (equity/ MF), normalization, validation, fingerprint dedup
├── accounting/       # FIFO lot engine, corporate-action application, realised/unrealised gains
├── performance/      # XIRR (stock/fund/portfolio), TWR, benchmark matching, historical valuations
├── backtesting/      # hypothetical-replacement simulation, cost model, look-ahead guard
├── mf/               # MF schemes/folios/NAV/SIP, overlap, allocation, consolidated exposure, rebalancing sim
├── mcp/              # registry, adapter, evidence cache, client (connects to external MCP servers)
├── research/         # LLM workflow, research-report generation, prompts, evidence citation
├── decision/         # risk signals (concentration, valuation, appreciation, earnings, governance), HOLD/MONITOR/TRIM/EXIT
├── portfolio/         # Stage 9: allocation drift, tax-aware rebalancing, tax lots, scenario comparison
├── ui/               # Streamlit pages
└── tests/            # unit/integration/property/golden tests
```

### Layer responsibilities

- **`config`**: single source of truth for app settings (`APP_ENV`, `DB_PATH`, `MCP_REGISTRY_PATH`, LLM keys/models), logging configuration, MCP server registry loader.
- **`core`**: primitive domain types — `InstrumentType` (EQUITY/MF), `Side` (BUY/SELL/SIP/REDEMPTION/SWITCH/DIVIDEND), `SignalType` (HOLD/MONITOR/TRIM/EXIT), `EventType` (split/bonus/rights/merger/demerger/buyback), money/date rounding helpers, money formatting.
- **`db`**: SQLAlchemy declarative `Base`, engine/session factories, Alembic migration env, repository abstractions over all tables (see `data_model.md`).
- **`importing`**: parsers for ICICI-style equity statement PDFs/XLS/TSV and MF consolidated/CAMS/Karvy statements; row normalisation into `NormalizedTransaction`; validation; SHA-256 fingerprint dedup; import audit trail.
- **`accounting`**: FIFO lot builder; application of corporate actions to lots; realised/unrealised gain computation; opening-position support; holdings reconstruction at an `as_of_date`.
- **`performance`**: stock-level, fund-level, and consolidated portfolio XIRR; TWR; benchmark-matched XIRR vs Nifty 50/500 TRI; historical portfolio valuation snapshots; auditable cash-flow records.
- **`backtesting`**: simulate replacing historical stock A with alternative stock B; transaction-cost model; dividend reinvestment option; look-ahead-bias guard via `as_of_date` cutoffs and verified-only corporate actions; actual vs hypothetical XIRR comparison.
- **`mf`**: MF scheme/folio/NAV/SIP modelling; MF XIRR; scheme overlap and underlying asset allocation; consolidated equity+MF exposure; optional annual review and tax-aware rebalancing simulation.
- **`mcp`**: MCP client manager (trusted registry, stdio + streamable_http), adapter that validates discovered tools before calling, evidence-envelope builder, `mcp_cache` read/write, graceful degradation/circuit breaker when a server is unreachable.
- **`research`**: LLM orchestrator (Gemini primary, OpenRouter fallback), prompt templates, research-report generation, evidence-envelope citation, report storage.
- **`decision`**: risk detectors (concentration, excessive valuation, rapid appreciation, earnings deterioration, governance), explainable HOLD/MONITOR/TRIM/EXIT signal generation with reasons and severity.
- **`portfolio`**: allocation analysis (asset class, sector, market-cap, direct-vs-MF) vs configurable targets; date-effective Indian tax rules (STCG/LTCG, exemption, STT, exit load); FIFO tax-lot reconstruction with missing-cost-basis flagging; deterministic cash-first, signal-aware, compounder-protecting rebalancing planner; annual/threshold/on-demand strategies; current-vs-proposed scenario comparison. Advisory only — no trades executed.
- **`scheduler`**: lightweight SQLite-backed scheduler (no external queue) with idempotent per-period job keys, crash recovery, and jobs for price/NAV refresh, corporate actions, research, alert evaluation and encrypted backups. CLI: `python -m scheduler`.
- **`alerts`**: deduplicated, configurable advisory alerts derived from Stage 8 signals, allocation breaches and data-health (stale/failed/reconciliation); in-app delivery with email/webhook stubs (logging only — never sent).
- **`security`**: Fernet-based encryption (PBKDF2 key derivation), secret/PII redaction for logs, and encrypted, sha256-verified SQLite backups with tested restore.
- **`monitoring`**: dependency-free metrics aggregation (scheduler, alerts, LLM usage, MCP cache, DB sizes) with Prometheus text export.
- **`ui`**: Streamlit pages for import, holdings, XIRR/performance, research reports, decision signals, MF overlap/allocation, rebalancing simulation, alerts, automation/monitoring and admin (corporate-action verification, MCP cache).
- **`tests`**: unit tests per module, integration tests across import→accounting→performance, golden fixtures for deterministic outputs, property tests for XIRR/convergence.


## 4. Dependency direction (enforced)

```
ui ─────► research/decision/mf/backtesting/performance/accounting/importing/db ─────► mcp (external servers)
                              ▲                                              ▲
                              └──────────────────── LLM is OUTSIDE ───────────┘
                                              trust boundary (no accounting)
```

- The LLM is called only from `research/` and `decision/`. Its outputs are stored as text/signal recommendations, never written into accounting tables.
- `mcp/` depends on no application module except `config` and `db` (for cache). It is a thin client layer.
- `importing/` depends on `core` and `db` only.
- No module imports from the research application at `/Users/anmol/stock-selection-agent`.

## 5. Concurrency and process model

- Single-process Streamlit app.
- MCP calls are asynchronous within a request (official `mcp` SDK sessions) but the UI remains synchronous/streamlit-native.
- Per-server concurrency limits come from the MCP registry (`max_concurrency`) and are honoured by the adapter via asyncio semaphores.
- No background workers, no task queue, no separate API server.

## 6. Security and trust boundaries

- MCP registry enforces **trusted targets only** (mirroring the research app's guardrail): stdio launchers and streamable_http URLs must be pre-approved; calling an undiscovered tool raises `ValueError`.
- API keys (Gemini, OpenRouter) live in `.env`, never committed.
- SQLite file permissions follow the OS user; no network exposure of the DB.
- Evidence envelopes record server/tool/args/timestamp so every LLM-cited fact is traceable to a source MCP call.

## 7. What is explicitly out of scope for Stage 0 (and this architecture)

- No feature implementation (code) — documentation only in Stage 0.
- No changes to `/Users/anmol/stock-selection-agent`.
- No microservices, message queues, Kubernetes, or multi-agent orchestration frameworks.
- No autonomous trading or order execution.
- No real-time pricing hard dependency — `nse-market` MCP is disabled in the existing registry; treated as a gap with fallback (see `mcp_integration.md` and `open_questions.md`).

## 8. Stage 10 — automation, reliability & production readiness

- **Process model**: the Streamlit app remains single-process; a *separate* scheduler process
  (`python -m scheduler --loop …`) runs refresh/backup/alert jobs. In Docker, `docker-compose.yml`
  runs them as two services sharing bind-mounted `data/`, `config/`, `logs/`.
- **Idempotency & recovery**: every job run is tracked in `data/scheduler.sqlite3` with a unique
  `(job_name, idempotency_key)`; keys are scoped (hourly/daily/weekly/monthly/quarterly). Interrupted
  runs are marked failed and retried; completed periods are never reprocessed.
- **Failure isolation**: refresh jobs never raise into the ledger; a failed MCP/API call degrades to a
  partial envelope or stale cache and leaves portfolio data untouched.
- **Security boundary**: secrets stay in `.env`; logs pass through a redaction filter; LLM prompts
  receive only sanitised external text; backups are Fernet-encrypted and verified before restore.
- **No external side effects**: email/webhook notifications are logging stubs; no autonomous trading.

## 9. Stage 12 — AI Investment Copilot

- **Tool-first orchestration**: `copilot/engine.py` sanitises input → routes intent
  (`copilot/intent.py`) → validates args (`copilot/security.py`) → executes an allowlisted
  read-only tool (`copilot/tools/*`) that wraps an existing deterministic engine → builds a
  cited, tiered answer (`copilot/response.py`).
- **No LLM arithmetic**: tool outputs are the authoritative figures; the LLM only routes intent
  (validated) and phrases explanations bounded to provided facts. The copilot is fully functional
  without an LLM (deterministic answers; research reported unavailable rather than fabricated).
- **Read-only & least privilege**: no tool mutates data or trades; all tools require `view`.
  Prompt-injection sanitisation, argument schema validation, rate limiting and redacted auditing apply.
- **Privacy**: conversation history is session-only (Streamlit `session_state`), capped and clearable;
  free-text tool arguments are redacted in logs.
- **UI**: an expander panel inside the existing five pages (no new top-level tabs). Context is built
  only when a question is submitted; suggestions are static, keeping page render fast.
- **Efficiency**: research is cached-first (fresh stored report avoids MCP/LLM calls); tools fetch only
  the data a question needs; evidence uses the Stage-10 MCP cache/circuit-breaker/size guard.



