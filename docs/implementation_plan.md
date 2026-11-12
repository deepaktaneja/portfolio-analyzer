# Implementation Plan — Development Stages, Tests, and Acceptance Criteria

**Stage 0 deliverable.** Phased roadmap for building the standalone portfolio intelligence application. Dependencies and acceptance criteria are defined per stage. No application code is implemented during Stage 0.

## 1. Guiding constraints

- Independent codebase, database, UI, and LLM workflows — no coupling to `/Users/anmol/stock-selection-agent`.
- No changes to the existing research application.
- No autonomous trading.
- LLM provides research/interpretation only; accounting is deterministic pure Python.
- MCP servers are reused directly as external services via the `mcp` SDK.

## 2. Phase roadmap

| Phase | Focus | Key deliverables | Depends on | Acceptance criteria |
|---|---|---|---|---|
| **0** (this stage) | Planning & architecture | 6 docs approved: architecture, data_model, mcp_integration, financial_methodology, implementation_plan, open_questions | — | Stakeholder sign-off on all six documents |
| **1** | Core platform | Repo scaffold, Python 3.12 venv, `mcp` SDK added, SQLAlchemy + Alembic, `config`, `core`, `db` modules, `.env` template | Stage 0 approval | Fresh install works; `alembic upgrade head` creates all tables; ruff + pytest pass |
| **2** | Import & accounting | Equity statement importers (PDF/XLS/TSV), normalisation, validation, fingerprint dedup; FIFO lot engine; corporate-action engine; holdings reconstruction | Phase 1 | ICICI-style statements import cleanly; holdings reconstruct; realised/unrealised gains match golden fixtures; dedup blocks duplicate imports |
| **3** | Performance | Stock/fund/portfolio XIRR, TWR, benchmark match (Nifty 50/500 TRI), historical valuations, auditable cash-flow records | Phase 2 | XIRR matches `pyxirr` reference on synthetic fixtures; deterministic across repeated runs; TWR/benchmark outputs sensible |
| **4** | Mutual funds | MF importers (consolidated/CAMS/Karvy), schemes/folios/NAV/SIP, MF XIRR, scheme overlap & allocation, consolidated equity+MF exposure | Phase 2 + MF MCP discovery | MF holdings import; per-scheme XIRR; overlap matrix generated; consolidated exposure report correct |
| **5** | MCP integration | Registry, adapter, evidence envelopes, `mcp_cache`, clients for company-research/bulk-screener/nse-history/mftool-mcp/TigZig(fallback); **MF/equity API discovery spike** (test free MF NAV API and free equity price/CA API as fallback) | Phase 1 + server discovery | Each server connects; undiscovered-tool call raises `ValueError`; evidence envelopes persisted & cited; cache TTL honoured; graceful degradation when a server is down; at least one working price/CA source confirmed |

| **6** | AI research | LLM orchestrator (Gemini primary, OpenRouter fallback), prompts, research-report generation, evidence citation | Phase 5 | Report generated for a holding with cited `evidence_ids`; LLM never alters accounting figures |
| **7** | Decision intelligence | Risk detectors (concentration, valuation, rapid appreciation, earnings deterioration, governance); HOLD/MONITOR/TRIM/EXIT signals with reasons & severity | Phase 3 + Phase 6 | Signals fire on seeded risk fixtures; explainable reasons; no autonomous orders |
| **8** | Rebalancing | Annual review workflow, tax-aware rebalancing simulation (STT, LTCG/STCG), what-if trades | Phase 4 | Rebalance sim produces trade list + estimated tax + post-trade exposure |
| **9** | Backtesting | Hypothetical replacement simulation, cost model, look-ahead guard, actual vs hypothetical XIRR | Phase 3 + Phase 5 (prices) | Backtest run reproducible; actual vs hypothetical XIRR compared; cost impact shown |
| **10** | UI | Streamlit pages: import, holdings, XIRR/performance, research, signals, MF overlap/allocation, rebalancing, admin (CA verification, MCP cache) | All prior | End-to-end flow works; golden-path E2E tests pass |
| **11** | Hardening | Test coverage ≥ 80%, property/golden tests, performance regression, release checklist | All prior | ruff clean; pytest green; release checklist complete |

## 3. Hard dependencies

- Phase 1 → every subsequent phase.
- MF MCP server discovery (`mftool-mcp` / TigZig) must complete before Phase 4 and Phase 5 can be scoped precisely.
- MCP server connectivity must be proven before Phase 5 implementation.

## 4. Test strategy

| Level | Scope | Tools |
|---|---|---|
| Unit | Each module in isolation (FIFO, XIRR, CA engine, import validation, signal detectors) | pytest |
| Integration | Import → accounting → performance end-to-end; MCP adapter connect + cache | pytest |
| Golden | Deterministic fixtures: known transactions + CAs → known holdings/gains/XIRR | pytest + JSON/CSV fixtures |
| Property | XIRR convergence, dedup idempotency, CA ordering invariance | hypothesis / pytest |
| E2E | Streamlit golden-path flows | pytest + streamlit testing |
| Regression | Performance benchmarks for XIRR and holdings reconstruction | pytest |

## 5. Acceptance definition (Definition of Done per stage)

- Code review passed.
- ruff lint clean (line-length 100, py3.12 target).
- Unit + integration tests green for the stage.
- Deterministic golden fixtures pass.
- Documentation updated (this docs/ set stays current as stages complete).
- No autonomous trading code present; signals are read-only/advisory.

## 6. Risks to track during implementation

| Risk | Mitigation |
|---|---|
| MCP server unreachable (esp. `nse-history`, `mftool-mcp`) | Aggressive caching + circuit-breaker + stale-cache fallback + manual CSV path |
| MF MCP contracts unknown | Discovery spike before Phase 4/5; do not assume tool names |
| Look-ahead bias in backtesting | Strict `as_of_date` cutoff; verified-only CAs; deterministic ordering |
| XIRR non-convergence / multiple IRR | Fixed tolerance + iteration cap; report solver status; fall back to TWR |
| LLM hallucination in research | Evidence envelopes + citations; LLM barred from accounting; accounting tested independently |

## 7. Exit criteria for Stage 0

All six documents under `docs/` exist, are internally consistent, and reflect the environment findings (existing portfolio-analyzer patterns, research-app MCP registry read-only reference, `mftool-mcp` primary / TigZig fallback for MF, `mcp` SDK not yet installed in venv). Sign-off obtained before Stage 1 begins.

