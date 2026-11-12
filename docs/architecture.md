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
- **`ui`**: Streamlit pages for import, holdings, XIRR/performance, research reports, decision signals, MF overlap/allocation, rebalancing simulation, admin (corporate-action verification, MCP cache).
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

