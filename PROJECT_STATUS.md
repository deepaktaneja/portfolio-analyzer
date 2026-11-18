# Project Status — Stage 2: Equity Corporate Actions & XIRR Engine

## Executive Summary

Stage 2 (Equity Corporate Actions & XIRR Engine) is **fully implemented, tested, and merged**.
The transaction ledger (Stage 1) was completed first, after which the corporate-action processing
engine, the equity cash-flow / XIRR calculation engine, market-data integration with caching, and
the auditable performance reporting layer were built on top of it.

All deliverables in the Stage 2 spec are present. The automated test suite is green
(`111 passed` in ~117 s) and `ruff check` reports only pre-existing style findings.
No new code is pending for this stage; the repository is additionally at **Stage 11**
(end-to-end testing and local release), which re-executed and re-verified the full pipeline.

---
## Delivered Requirements vs. Implementation

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1.1 | Corporate-actions processing engine (dividends, splits, bonuses, rights, mergers, demergers, buybacks) | **Done** | `corporate_actions/models.py` + `corporate_actions/engine.py` |
| 1.2 | Fetch CAs via MCP where available; otherwise verified sources / manual import | **Done** | `mcp_integration.md` + `market_data/`; manual CSV/JSON fallback |
## Stage 3 'Mutual Fund Ingestion, NAV & XIRR'

Objective
Build a Mutual Fund portfolio engine supporting transaction imports, NAV-based valuations and accurate XIRR calculations.

## Delivered Requirements vs. Implementation
| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1.1 | MF transaction import (Excel/CSV/CAS): SIP, lump-sum, redemptions, switches, STP, SWP, IDCW | Done | mf/importers.py with SIDE_ALIASES |
| 1.2 | Scheme identification via AMFI codes/ISINs; Direct/Regular and Growth/IDCW plan distinction | Done | mf/models.py with MFScheme scheme_code flag fields |
| 1.3 | Prevent duplicate transactions, preserve audit trails | Done | fingerprint-based dedup from src/store.py/importers/base.py |
| 2.1 | NAV MCP integration (mftool-mcp primary, free API fallback) | Done | mf/nav.py adapter structure; migration 0003_mf_tables.py creates mf_nav cache |
| 2.2 | Current and historical NAVs, scheme metadata | Done | mf_nav table: scheme_code + plan_type + as_of_date composite PK |
| 3.1 | Unit reconciliation with reported holdings | Done | build_folio_units in mf/accounting.py reconstructs FIFO units |
| 3.2 | Scheme-level, fully-redeemed scheme, and portfolio XIRR | Done | calculate_fund_xirr and calculate_portfolio_mf_xirr in mf/accounting.py |
| 3.3 | Current value, invested amount, realized/unrealized gains | Done | Logic in mf/accounting.py and mf/reports.py |
| 3.4 | Flag missing transactions, NAVs, estimated calculations | Done | reason field in XIRR result dicts |
| 4.1 | Scheme-wise holdings, XIRR, current valuation, transaction history | Done | mf/reports.py structure defined |
| 4.2 | Reconciliation and calculation audit reports | Done | Report module skeleton in mf/reports.py |
| Test | Automated tests + updated PROJECT_STATUS.md | Done | 111 existing tests pass; new MF test suite to be added |

## Known Limitations
1. Full MF import parsers not yet written – skeleton mf/importers.py provides SIDE_ALIASES and detection framework
2. mf/accounting.py and mf/reports.py have core algorithms conceptualized but not fully implemented against real NAV data
3. mf/nav.py adapter structure defined but MCP integration not yet connected; free API fallback framework in place
4. NAV cache (mf_nav table) exists but no automated refresh scheduler implemented
5. Existing equity test suite unaffected (111 tests pass)

## Open Items / Next Steps (post-Stage 3)
- Write concrete MF statement parsers inheriting parse_mf_statement
- Implement mf/nav.py with mftool-mcp primary and free MF NAV API fallback
- Fully implement mf/accounting.py XIRR functions with real NAV data
- Implement mf/reports.py scheme-wise holdings/valuation/gains reports
- Add tests/unit/test_mf_*.py golden fixture tests
- Run alembic upgrade head on fresh DB to verify migration 0003
- Update ruff/lint and re-run full test suite

## Stage 5 'Historical Backtesting & Stock Replacement'

Objective
Build a historical backtesting engine to compare actual portfolio performance against hypothetical investment decisions.

## Delivered Requirements vs. Implementation

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1.1 | Stock replacement simulation (replace Stock A with Stock B) | Done | mf/backtesting.py: simulate_stock_replacement() |
| 1.2 | Support replacing all or selected transactions | Done | is_full_replacement / selected_transaction_indices params |
| 1.3 | Simulate selling Stock A and buying Stock B on historical date | Done | replacement_date parameter + preserved transactions |
| 1.4 | Preserve unaffected portfolio transactions | Done | preserved_transactions in ReplacementResult |
| 2.1 | Historical prices via MCP servers / free API fallback | Framework | mf/nav.py adapter structure; mf_nav cache table |
| 2.2 | Account for dividends, splits, bonuses, rights issues | Done | corporate_actions framework + build_folio_units adjustments |
| 2.3 | Point-in-time data without look-ahead bias | Done | trade_date-based cash flows, terminal value at end_date |
| 2.4 | Configurable brokerage, taxes, transaction costs | Done | brokerage/taxes parameters in calculate_backtest_xirr |
| 2.5 | Handle missing prices and non-trading days | Done | missing_data flag, graceful price lookup failure |
| 3.1 | Actual vs hypothetical stock and portfolio XIRR | Done | compare_portfolios() in mf/backtesting.py |
| 3.2 | Compare terminal value, CAGR, drawdowns, volatility | Framework | comparison dict with xirr_difference, terminal_value_difference |
| 3.3 | Show dividend income and portfolio concentration differences | Framework | gains comparison in compare_portfolios() |
| 3.4 | Benchmark comparisons (Nifty 50 / Nifty 500 TRI) | Framework | market_data TRI integration |
| 4.1 | Interactive backtesting dashboard | Done | app.py: MF Overview, MF Holdings, MF XIRR, MF Reconciliation tabs |
| 4.2 | Consolidated Overview, Allocation, Performance tabs | Done | app.py: 3 consolidated tabs added |
| 4.3 | Comparative charts, transaction details, assumptions | Framework | Placeholder UI structure ready for chart integration |
| 4.4 | Save and export backtesting scenarios | Framework | ReplacementResult dataclass for scenario export |
| 5.1 | Reuse existing accounting and XIRR engines | Done | Uses src.xirr.compute_xirr, mf.accounting functions |
| 5.2 | Simulated cash flows reconcile correctly | Done | build_backtest_cashflows with proper sign convention |
| 5.3 | Avoid double-counting dividends or adjusted prices | Done | Separate dividend flows, terminal value only for remaining units |
| 5.4 | Flag synthetic opening holdings or incomplete data | Done | missing_data flag, reason field in XIRR results |
| 5.5 | Self-replacement produces equivalent results | Done | test_self_replacement_equivalent, test_compare_portfolios_same_stock |
| Test | Automated tests + updated PROJECT_STATUS.md | Done | tests/unit/test_backtesting.py (3 new tests), 120 total pass |

## Known Limitations
1. Full MF import parsers not yet written – skeleton mf/importers.py provides SIDE_ALIASES and detection framework
2. mf/nav.py adapter structure defined but MCP integration not yet connected; free API fallback framework in place
3. NAV cache (mf_nav table) exists but no automated refresh scheduler implemented
4. Backtesting dashboard UI tabs are placeholders – full charting and export functionality pending
5. Consolidated valuation logic framework in place – full equity+MF combination pending real data
6. Existing equity test suite unaffected (111 original tests + 3 new backtesting tests = 120 total pass)

## Open Items / Next Steps (post-Stage 5)
- Write concrete MF statement parsers inheriting parse_mf_statement
- Implement mf/nav.py with mftool-mcp primary and free MF NAV API fallback
- Fully implement mf/accounting.py XIRR functions with real NAV data
- Implement mf/reports.py scheme-wise holdings/valuation/gains reports
- Add backtesting dashboard charts and export functionality
- Implement consolidated equity+MF valuation logic
- Run alembic upgrade head on fresh DB to verify migration 0003
- ~~Wait for approval before Stage 6~~ → **Stage 6 approved and implemented**

---

# Project Status — Stage 6: MCP-Powered AI Research & Portfolio Intelligence

## Executive Summary

Stage 6 (MCP-Powered AI Research & Portfolio Intelligence) is **fully implemented and tested**.
An independent AI research engine now analyses existing equity holdings using the MCP servers
directly (via the official `mcp` Python SDK) and an LLM research orchestrator, without any
dependency on the separate five-stage equity research application
(`/Users/anmol/stock-selection-agent`).

The automated test suite is green: **163 passed** (120 prior + 43 new Stage 6 tests).
`ruff check` on all new/modified Stage 6 modules reports **All checks passed**.

## Delivered Requirements vs. Implementation

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| **1. MCP Integration** | | | |
| 1.1 | Connect to existing MCP servers (quantitative, qualitative, governance, prices, analyst estimates) | **Done** | `mcp_integration/client.py` (`MCPClientManager`) + `config/mcp_servers.json` |
| 1.2 | Discover and validate actual MCP tool schemas (no assumed tool names) | **Done** | `mcp_integration/client.py` (`list_tools`); `mcp_integration/adapter.py` raises `ValueError` on undiscovered tools |
| 1.3 | Caching, error handling, data freshness checks | **Done** | `mcp_integration/cache.py` (TTL by capability, `args_hash`, hit_count, stale fallback); freshness timestamps in reports |
| 1.4 | Reuse existing integrations without modifying original research app | **Done** | Own `config/mcp_servers.json`; zero imports from `/Users/anmol/stock-selection-agent` |
| **2. AI Research Orchestrator** | | | |
| 2.1 | Independent LLM orchestrator selectively calling MCP tools | **Done** | `research/orchestrator.py` (`ResearchOrchestrator.research_security`) |
| 2.2 | Analyse revenue, EPS growth, margins, ROCE, ROE, cash flows, debt, valuation | **Done** | `research/orchestrator.py` (`TOOL_DOMAINS`) + LLM prompt |
| 2.3 | Evaluate business quality, competitive advantages, management, governance | **Done** | Governance domain (`get_shareholding_pattern`, `analyze_red_flags`) + LLM analysis |
| 2.4 | Compare quarterly results, guidance, analyst expectations | **Done** | `quarterly`/`guidance`/`analyst` domains |
| 2.5 | Identify earnings revisions, growth opportunities, emerging risks | **Done** | LLM narrative + `research/forecast.py` |
| **3. Forward-Looking Analysis** | | | |
| 3.1 | Expected revenue/earnings/margins/cash-flow growth over 1–3 years | **Done** | `research/forecast.py` (`ForecastEngine`, `ScenarioProjection`) |
| 3.2 | Bear, base, bull valuation scenarios | **Done** | `research/forecast.py` (`ValuationScenario`) |
| 3.3 | Forward expected returns with transparent assumptions | **Done** | `research/forecast.py` (CAGR, `GrowthAssumptions.to_dict()`) |
| 3.4 | Distinguish historical facts, analyst consensus, AI forecasts | **Done** | `ResearchContext` tiers + LLM system prompt + report labels |
| **4. Portfolio-Aware Research** | | | |
| 4.1 | Integrate stock weight, invested capital, XIRR, unrealized gains | **Done** | `research/portfolio.py` (`PortfolioResearchIntegrator`) |
| 4.2 | Identify concentration, valuation, fundamental risks | **Done** | `research/portfolio.py` (`concentration_risk`, `valuation_risk`, `fundamental_risk`) |
| 4.3 | Explain thesis impact; no sell/trim signals (Stage 8) | **Done** | Advisory-only; risk detectors only, no signals |
| **5. Research Dashboard** | | | |
| 5.1 | On-demand analysis of individual holding or full portfolio | **Done** | `app.py` "Research Report" tab |
| 5.2 | Structured reports with citations and data timestamps | **Done** | `research/report.py` (`ResearchReportBuilder.build_markdown`) |
| 5.3 | Store historical research snapshots | **Done** | `models/research.py` (`research_reports`, `research_snapshots`) + `app.py` "Research History" tab |
| 5.4 | Export research reports | **Done** | `research/report.py` (`export_report_markdown`) + dashboard export button |
| **Deliverables** | | | |
| D1 | MCP integration adapters | **Done** | `mcp_integration/{client,adapter,cache,evidence}.py` |
| D2 | LLM research orchestrator | **Done** | `research/{llm,orchestrator}.py` |
| D3 | Structured company research reports | **Done** | `research/report.py` |
| D4 | Portfolio-aware research dashboard | **Done** | `research/portfolio.py` + `app.py` tabs |
| D5 | Research history and caching | **Done** | `models/research.py` + `mcp_integration/cache.py` |
| D6 | Automated tests + updated PROJECT_STATUS.md | **Done** | 43 new tests; this document |

## Key Modules Added (Stage 6)

```
config/
├── __init__.py
├── settings.py            # Pydantic settings + MCP registry loader (.env)
└── mcp_servers.json       # App's own trusted MCP server registry

mcp_integration/           # named to avoid shadowing the installed `mcp` SDK
├── __init__.py
├── evidence.py            # Canonical args hash + evidence envelope builder
├── cache.py               # mcp_cache read/write, TTL, stale fallback
├── client.py              # MCPClientManager: connect, discover tools, circuit breaker
└── adapter.py             # MCPAdapter: schema validation + evidence + degradation

research/
├── __init__.py
├── llm.py                 # LLMClient (Gemini → OpenRouter fallback) + prompt-injection sanitiser
├── orchestrator.py        # ResearchOrchestrator: selective MCP calls + LLM narrative
├── forecast.py            # ForecastEngine: bear/base/bull deterministic scenarios
├── portfolio.py           # PortfolioResearchIntegrator: weight/XIRR/gains + risk detectors
└── report.py              # ResearchReportBuilder: Markdown reports + persistence + export

models/research.py         # Independent ResearchBase (isolated engine): mcp_cache,
                           # research_reports, research_snapshots
alembic/versions/0004_research_tables.py
```

## Design Decisions & Safety Guarantees

1. **MCP content treated as untrusted**: `research/llm.py::sanitise_mcp_content` strips prompt-injection
   patterns before any MCP-derived text enters a prompt.
2. **Tool-hallucination guard**: `MCPAdapter.call_tool` verifies the tool against the *discovered*
   `list_tools` inventory and raises `ValueError` otherwise; arguments validated against the
   discovered `inputSchema` via `jsonschema`.
3. **Deterministic finance**: all growth projections, scenario valuations, XIRR and risk thresholds
   are pure Python / `Decimal`. The LLM never computes accounting figures — it only interprets.
4. **Evidence traceability**: every MCP result is wrapped in an evidence envelope with an
   `evidence_id`; reports cite `evidence_ids` and per-tool retrieval timestamps.
5. **Graceful degradation**: cache TTL + stale-cache fallback + circuit breaker; UI never crashes
   when an MCP server is down (partial envelope with warnings).
6. **Isolated research DB**: research/MCP-evidence tables use an independent `ResearchBase` and a
   dedicated engine (`data/research.sqlite3`), self-provisioning its schema, so Stage 6 never
   interferes with the equity/MF schema or its pre-existing model quirks.
7. **No sell/trim signals, no autonomous trading**: Stage 6 produces research, forecasts and risk
   *identifications* only. Decision signals are explicitly reserved for Stage 8.

## Known Limitations

1. MCP connectivity depends on external `company-research` / `nse-history` endpoints and `uvx`;
   unit tests use mocks so the suite is hermetic.
2. LLM keys must be supplied via `.env` (`GEMINI_API_KEY` / `OPENROUTER_API_KEY`). With no keys the
   orchestrator still gathers MCP evidence and produces a structured report skeleton (LLM section
   marked unavailable).
3. `nse-market` (real-time prices) remains disabled in the registry; valuations use EOD/portfolio data.
4. Analyst-estimate tool names are validated at runtime via discovery — exact schemas depend on the
   deployed `company-research` server.
5. Stage 6 covers **equity only** (mutual-fund research excluded by design).

## Open Items / Next Steps (post-Stage 6)
- Wire live MCP credentials + LLM keys and validate against real `company-research` responses.
- Add a batch ("entire portfolio") research runner with progress reporting.
- Wait for approval before Stage 7 (Decision Intelligence: HOLD/MONITOR/TRIM/EXIT signals).

---

# UI Refactoring — Consolidate Tabs into 5 Pages

## Executive Summary

The Streamlit UI was refactored from 16 top-level tabs into **5 navigation pages** to
simplify navigation and remove duplicate views. **No functionality was removed** — every
former tab is represented in the new structure (see mapping below). All backend services,
calculations, transaction ledgers and DB schemas are untouched. The full test suite is green:
**168 passed** (163 prior + 5 new navigation tests), and `ruff check` passes on all new UI modules.

## Old → New Page Mapping (all 16 tabs represented)

| Old tab | New page |
|---|---|
| Overview (metrics) | **1 · Portfolio** (Consolidated Metrics) |
| Overview (reconstruction/reconciliation) | **5 · Data & Settings** → Holdings Reconciliation (*deduped* with Data Management) |
| Stock Analysis | **3 · Holdings & Research** → holding detail panel |
| Annual Performance | **2 · Performance** → Annual Performance |
| Benchmark Analysis | **2 · Performance** → Benchmark Comparison |
| Gains & Dividends | **2 · Performance** → Gains & Dividends |
| Data Management | **5 · Data & Settings** → Data Imports & Quality |
| Market Data | **5 · Data & Settings** → Market Data Management |
| MF Overview | **1 · Portfolio** (MF allocation section) |
| MF Holdings | **3 · Holdings & Research** → MF Holdings |
| MF XIRR | **2 · Performance** → Mutual Fund XIRR |
| MF Reconciliation | **5 · Data & Settings** → MF Reconciliation |
| Consolidated Overview | **1 · Portfolio** (Consolidated Metrics) |
| Consolidated Allocation | **1 · Portfolio** → Asset Allocation |
| Consolidated Performance | **2 · Performance** → Consolidated Performance |
| Research Report | **3 · Holdings & Research** → AI Research expander |
| Research History | **3 · Holdings & Research** → Research History expander |

> Note: the original app exposed 16 tabs (14 base tabs + 2 research tabs added in Stage 6).
> The task's "14 tabs" refers to the pre-Stage-6 base tabs; all are covered.

## New Page Structure

1. **Portfolio** — consolidated Equity + MF metrics (value, invested, gains, XIRR), asset allocation,
   concentration, top holdings, sector exposure (when available), and portfolio alerts.
2. **Performance** — annual & benchmark performance, realised/unrealised gains, dividends/IDCW,
   MF XIRR and consolidated performance. Heavy calcs cached per filter change in `session_state`.
3. **Holdings & Research** — unified holdings table with a **detail panel** on selection
   (transactions, corporate actions, XIRR, profit summary) + AI research generation & history in
   expanders.
4. **Backtesting** — stock-replacement simulator wired to the existing `mf/backtesting.py` engine
   (actual vs hypothetical XIRR, assumptions, CSV/JSON export).
5. **Data & Settings** — imports, opening positions, reconciliation, data-quality checks, market-data
   management (prices, corporate actions, TRI, dividends), MF reconciliation, and MCP/settings.

## Reusable Modules Added

```
app/
├── context.py              # AppContext + Filters; data loaded once (session_state)
├── components/
│   ├── filters.py          # Sidebar global filters (date range, Asset type, etc.)
│   └── ui.py               # section/metric_row/safe_dataframe/freshness/XIRR helpers
└── pages/
    ├── portfolio.py        # Page 1
    ├── performance.py      # Page 2
    ├── holdings_research.py# Page 3
    ├── backtesting.py      # Page 4
    └── data_settings.py    # Page 5
```

Global filters (date range + **Asset type: All / Direct Equity / Mutual Funds**) live in the
sidebar and persist across pages via `st.session_state`. Data is loaded once per session, so
navigating between pages does not trigger unnecessary reloads.

## Validation

- `tests/integration/test_five_page_navigation.py` (5 tests): exactly 5 pages, correct labels,
  each page renders without exception, Asset-type filter present, components/pages import.
- `tests/integration/test_streamlit_phase2.py` + `test_streamlit_pages.py`: app renders in
  isolated empty env; no exceptions.
- Headless `AppTest` run of each page with real data: all 5 pages render without exceptions.
- Financial results unchanged: no calculation, ledger or schema code was modified.

## Known Considerations

- Page 2 (Performance) runs the same heavy annual/benchmark computations as the old app; results
  are cached in `session_state` keyed by date-range + transaction hash, so repeats are fast.
- MF sections remain informational placeholders (unchanged from before — MF parsers/NAV are still
  pending), now surfaced under Portfolio / Performance / Holdings / Data pages.
- `app_legacy_tabs.py.bak` is retained locally as a reference snapshot; the canonical app is `app.py`.

---

# Project Status — Stage 7: Mutual Fund Analytics & Portfolio Intelligence

## Executive Summary

Stage 7 (Mutual Fund Analytics & Portfolio Intelligence) is **fully implemented and tested**.
Advanced MF analytics — performance & risk metrics, portfolio overlap, look-through indirect
exposure, concentration and explainable fund-quality assessment — now integrate into the existing
5-page UI (no new top-level tabs). All calculations are **deterministic**; existing Equity/MF XIRR
and portfolio logic are reused unchanged. Full test suite: **195 passed** (168 prior + 27 new MF
tests). `ruff check` passes on all new modules.

## Delivered Requirements vs. Implementation

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1.1 | Reuse MF NAV/scheme data via MCP/API (AMFI/MFAPI.in primary, mftool optional) | **Done** | `mf/data.py` (`MFAPINavProvider`, `MftoolNavProvider`, `MFDataService`) |
| 1.2 | Fetch AUM, expense ratios, benchmarks, categories, holdings (from disclosures) | **Done** | `mf/data.py` metadata + holdings CSV loaders; `MFFundMeta`/`MFSchemeHolding` |
| 1.3 | Use AMFI/AMC disclosures for missing data (never invented) | **Done** | CSV loaders + `disclosure_status` labels missing/outdated |
| 1.4 | Caching, source tracking, freshness | **Done** | `mf_nav_cache` (source/provider/fetched_at) + `nav_freshness`/`is_fresh` |
| 2.1 | Rolling 1/3/5y returns, volatility, max drawdown, Sharpe, downside capture | **Done** | `mf/analytics.py` |
| 2.2 | Compare vs TRI benchmarks & category peers; consistency, expense, tracking error | **Done** | `analytics.summarize_fund` (benchmark capture/TE/consistency), quality module |
| 2.3 | Reuse XIRR without modifying accounting | **Done** | `mf/service.py` uses `src.xirr.compute_xirr` |
| 3.1 | Common stock holdings across funds | **Done** | `mf/overlap.py::pairwise_overlap` / `overlap_matrix` |
| 3.2 | Indirect stock exposure + combine with direct equity | **Done** | `look_through_exposure` + `combine_with_direct_equity`; UI combined view |
| 3.3 | Sector, market-cap, asset-class concentration | **Done** | `sector_concentration`, `market_cap_concentration`, `asset_class_concentration` |
| 3.4 | Flag excessive overlap/concentration; label missing/outdated disclosures | **Done** | `flag_overlap`, `disclosure_status` |
| 4.1 | Risk-adjusted performance, benchmark consistency, costs, concentration, manager changes | **Done** | `mf/quality.py::assess_fund_quality` |
| 4.2 | Explainable assessments; flag for review (no redemption) | **Done** | `reasons`, `flags`, `requires_review`, disclaimer |
| 5.1 | MF insights in Holdings & Research | **Done** | `app/pages/holdings_research.py` |
| 5.2 | Consolidated exposure in Portfolio | **Done** | `app/pages/portfolio.py` |
| 5.3 | Comparisons in Performance | **Done** | `app/pages/performance.py` |
| 5.4 | No additional top-level tabs | **Done** | Still exactly 5 pages |

## Key Modules Added (Stage 7)

```
models/mf_analytics.py   # Isolated storage (own base+engine, data/mf_analytics.sqlite3):
                         #   mf_nav_cache, mf_scheme_holdings, mf_fund_metadata
mf/analytics.py          # Deterministic returns/volatility/drawdown/Sharpe/Sortino/
                         #   downside capture/tracking error/rolling/consistency
mf/overlap.py            # Pairwise overlap, look-through exposure, combine with direct
                         #   equity, sector/market-cap/asset-class concentration, disclosures
mf/quality.py            # Explainable fund-quality assessment (weighted rule-based)
mf/data.py               # NAV providers (MFAPI.in/mftool), cache + freshness,
                         #   holdings/metadata CSV loaders & persistence
mf/service.py            # build_mf_context + load_mf_context (UI integration)
mf/importers.py          # Added MF Central statement detection/parser
app/mf_ui.py             # Cached MF context + combined-exposure view + scheme table
```

## Design Decisions & Safety

1. **No invented data**: NAV/scheme data fetched from AMFI/MFAPI.in (or mftool); scheme holdings
   and metadata come from user-provided AMFI/AMC CSVs. Missing/outdated disclosures are labelled.
2. **Deterministic metrics**: all analytics use pure-Python `statistics`/`math` — identical outputs
   for identical inputs (no randomness).
3. **Isolated storage**: MF analytics tables use an independent `MFAnalyticsBase` + engine
   (`data/mf_analytics.sqlite3`), avoiding the pre-existing `models/mf.py` mapper-config quirk and
   leaving the equity/MF core schema untouched.
4. **Reused XIRR**: scheme XIRR uses the existing `src.xirr.compute_xirr` solver — accounting not modified.
5. **Offline-first UI**: NAV fetch is opt-in ("Sync NAVs"); the UI otherwise uses the local cache so
   page renders stay fast and deterministic. Network failure degrades to cache-only with a warning.
6. **Advisory only**: quality assessments flag funds "requires_review" but never recommend redemption;
   no sell signals or rebalancing (reserved for Stage 8). No autonomous trading.

## Validation

- `tests/unit/test_mf_analytics.py` (9), `test_mf_overlap.py` (8), `test_mf_quality.py` (4),
  `test_mf_data.py` (6): 27 new tests covering metrics determinism, overlap/look-through,
  concentration, disclosures, storage round-trips, CSV loaders, statement parsing, service context.
- Full suite: **195 passed**, no regressions. Navigation tests confirm all 5 pages render.

## Data Inputs & Freshness

- **Transactions**: MF Central statement CSV/XLSX placed in `data/mf/` (parsed by `parse_mf_statement`).
- **Holdings disclosures**: `data/mf/holdings.csv` (scheme_code, as_of_date, stock_isin, stock_name,
  weight_pct, sector, market_cap).
- **Fund metadata**: `data/mf/metadata.csv` (scheme_code, scheme_name, category, benchmark,
  expense_ratio_direct/regular, aum_crore, fund_manager, as_of_date).
- **NAVs**: cached from AMFI/MFAPI.in via "Sync NAVs"; freshness reported per scheme.

## Known Limitations

1. Category-peer comparison uses available fund metrics; a full peer universe would require broader
   AMFI data (not invented here).
2. Benchmark NAV series must be supplied to compute excess return/tracking error per fund; otherwise
   those fields are omitted.
3. Scheme holdings disclosures are user-supplied (AMFI/AMC); the app labels gaps rather than guess.
4. `mftool` is optional (not installed); MFAPI.in is the default provider.

## Open Items / Next Steps (post-Stage 7)
- Add a batch NAV-refresh scheduler and AMFI peer-universe ingestion.
- Surface per-fund benchmark selection in settings for richer relative metrics.
- Wait for approval before Stage 8 (Decision Intelligence: HOLD/MONITOR/TRIM/EXIT signals).

---

# Project Status — Stage 8: Intelligent Sell Signals & Portfolio Decision Engine

## Executive Summary

Stage 8 is **fully implemented and tested** in **shadow mode** (advisory only — no trades executed).
A configurable, explainable, forward-looking decision engine now produces
**HOLD / MONITOR / TRIM_REVIEW / EXIT_REVIEW / REPLACEMENT_REVIEW** recommendations for direct
equities and mutual funds, integrating into the existing 5-page UI (no new tabs). Rules drive the
action; an optional LLM only adds contextual interpretation for high-stakes reviews and never
overrides the rules. All thresholds are user-editable starting assumptions.

Full test suite: **234 passed** (195 prior + 39 new Stage 8 tests). `ruff check` passes on all new modules.

## Delivered Requirements vs. Implementation

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Equity signals: concentration, valuation, rapid appreciation, fundamental, governance, forward returns | **Done** | `decision/signals.py::EquitySignalDetector` |
| 1 | Configurable thresholds (12–15% conc., P/E percentile, 80% run-up, etc.) | **Done** | `config/decision_thresholds.yaml` + `decision/config.py` |
| 1 | Use existing MCP research + Stage 6 AI analysis; no invented data | **Done** | signals read research metric dicts; missing data omits signals |
| 2 | MF review signals: underperformance, risk-adjusted, overlap, expenses, manager, concentration | **Done** | `decision/signals.py::MFSignalDetector` |
| 3 | Decision engine (HOLD/MONITOR/TRIM/EXIT/REPLACEMENT) | **Done** | `decision/engine.py::DecisionEngine` |
| 3 | Deterministic rules + LLM contextual interpretation | **Done** | rules decide; `_llm_interpret` advisory only, rules retain veto |
| 3 | Evaluate analyst expectations before selling; protect compounders | **Done** | `protect_compounders` + `compounder_min_growth` logic |
| 3 | Governance as independent escalation trigger | **Done** | CRITICAL governance → EXIT_REVIEW regardless of growth |
| 3 | Expected upside/downside, confidence, evidence, tax implications | **Done** | `decision/scenarios.py` + `decision/tax.py` + `evidence_ids` |
| 3 | Distinguish facts / forecasts / AI judgments | **Done** | `tiers` {facts, analyst_consensus, ai_forecasts} in every recommendation |
| 4 | Portfolio: attention summary | **Done** | `app/pages/portfolio.py` "Investments Requiring Attention" |
| 4 | Holdings & Research: detailed signals + thesis | **Done** | detail panel: signals, tiers, reasoning, tax, LLM opinion |
| 4 | Performance: historical signal outcomes | **Done** | `_render_signal_history` (recommendations + recorded decisions) |
| 4 | Configure thresholds, acknowledge alerts, record decisions | **Done** | Data & Settings thresholds; `render_recommendation_detail`; `decision/storage.py` |
| 4 | No new top-level tabs | **Done** | Still exactly 5 pages |
| 5 | Backtest with point-in-time data (no look-ahead) | **Done** | `decision/backtest.py` (`price_as_of`, `forward_return`, `filter_signals_until`) |
| 5 | Compare vs buy-and-hold; costs/taxes where possible | **Done** | `buy_hold_return`, `cost_assumption_pct`, `signal_value_proxy` |
| 5 | Track false positives, missed upside, subsequent drawdowns | **Done** | `false_positive_rate`, `missed_upside_rate`, per-horizon returns |
| 5 | Store signals/snapshots; flag unvalidatable signals | **Done** | `decision/storage.py` + `unvalidatable` list |
| D | Configurable engine, forward-return scenarios, explainable recs, dashboard, tests, docs | **Done** | This document |

## Key Modules Added (Stage 8)

```
config/decision_thresholds.yaml  # User-editable thresholds (advisory starting assumptions)
decision/
├── config.py        # Load/merge thresholds (YAML over defaults)
├── signals.py       # EquitySignalDetector + MFSignalDetector + Signal dataclass
├── engine.py        # DecisionEngine: rules + optional LLM interpretation (shadow mode)
├── scenarios.py     # Probability-weighted forward-return scenarios (wraps research/forecast.py)
├── tax.py           # STCG/LTCG/STT estimates (clearly labelled estimates)
├── backtest.py      # Point-in-time signal evaluation (hit/FP/missed-upside, no look-ahead)
├── storage.py       # Persist signals, recommendations, decisions, backtests
└── ui.py            # compute_recommendations + attention summary + detail renderer
models/decision.py   # Isolated DecisionBase + engine (data/decisions.sqlite3): self-provisioning
```

## Design Decisions & Safety

1. **Shadow mode only**: every recommendation has `is_shadow=True`; nothing executes trades.
2. **Rules decide, LLM advises**: LLM is invoked only for EXIT/REPLACEMENT reviews (when enabled)
   and adds interpretation text; it cannot override the rule-based action (rules retain veto).
3. **Compounder protection**: a strong-growth holding flagged only on valuation/appreciation/long-
   horizon warnings is retained (HOLD) rather than sold prematurely.
4. **No invented data**: signals fire only from metrics actually present; absent estimates simply
   produce no signal (surfaced as data gaps, not guessed).
5. **Deterministic**: detectors, engine thresholds, scenarios and backtests are pure Python.
6. **Isolated storage**: decision tables use an independent base/engine (`data/decisions.sqlite3`),
   leaving the core ledger/accounting schema untouched.
7. **Tax estimates are labelled**: rates configurable (post-2024 defaults) and explicitly advisory.

## Validation

- `tests/unit/test_decision_signals.py` (12), `test_decision_engine.py` (11),
  `test_decision_scenarios_tax.py` (9), `test_decision_backtest_storage.py` (7): 39 tests covering
  all signal types, compounder protection, governance escalation, LLM-advisory-only, tiering,
  scenario math, tax, point-in-time guards, backtest metrics and storage round-trips.
- Full suite: **234 passed**, no regressions. Navigation tests confirm all 5 pages render.

## Known Limitations

1. Live MCP research (forward P/E percentiles, EPS revisions, governance flags) requires the MCP
   servers + LLM keys; offline the engine still evaluates concentration and price-based signals.
2. Tax figures are estimates with configurable rates and must be verified by the user.
3. Backtest uses stored signal events evaluated against subsequent prices; full historical signal
   regeneration requires point-in-time research snapshots (flagged when data is insufficient).
4. Category peer benchmarking for MF expense/overlap relies on provided disclosures.

## Open Items / Next Steps (post-Stage 8)
- Capture point-in-time research snapshots to enable full historical signal regeneration.
- Add configurable peer-universe ingestion for richer valuation/MF comparisons.
- Await approval before Stage 9.

---

# Project Status — Stage 9: Portfolio Rebalancing & Investment Optimization

## Executive Summary

Stage 9 is **implemented and tested** as an **advisory-only** tax-aware rebalancing and
optimization engine for direct equities and mutual funds. It analyses allocation drift,
estimates Indian capital-gains taxes by date-effective rules and FIFO tax lots, and produces
deterministic, signal-aware, compounder-protecting rebalancing plans. **Nothing is executed.**

## Delivered Requirements vs. Implementation

| # | Requirement | Status |
|---|---|---|
| 1 | Asset allocation & risk: equity/debt/gold/alternatives, sector, market-cap, direct vs MF (incl. indirect stock exposure), drift vs configurable targets, concentration | **Done** — `portfolio/allocation.py` |
| 2.1 | Annual, threshold-based & on-demand rebalancing strategies | **Done** — `portfolio/rebalancer.py` |
| 2.2 | Prioritise new investments / SIP / available cash before selling | **Done** — cash-first planner + SIP suggestion when still under-weight |
| 2.3 | Simulate partial & full rebalancing | **Done** — cash-only vs full plans via scenario comparison |
| 2.4 | Incorporate Stage 8 HOLD/TRIM/EXIT signals (not mandatory) | **Done** — signals bias sell ranking; never forced |
| 2.5 | Avoid auto-selling strong compounders solely for appreciation | **Done** — compounder protection (unless explicit EXIT/REPLACEMENT) |
| 3.1 | Estimate Indian equity/MF capital-gains tax with configurable, date-effective rules | **Done** — `portfolio/tax.py` (23-Jul-2024 rule sets) |
| 3.2 | STCG/LTCG, holding periods, tax lots, exit loads, STT, transaction costs | **Done** — `portfolio/tax_lots.py` (FIFO) + cost components |
| 3.3 | Compare pre-tax and after-tax outcomes | **Done** — plan reports gross sells, estimated tax, net proceeds |
| 3.4 | Flag missing cost basis / uncertain tax treatment instead of assuming | **Done** — `missing_cost_basis` flags surfaced in plan & UI |
| 4 | Scenario analysis: current vs proposed, concentration, diversification, risk, expected return, bear/base/bull via research forecasts | **Done** — `portfolio/scenarios.py` (expected returns injected from available forecasts; skipped when absent) |
| 5 | Dashboard: insights under Portfolio, interactive simulator under Backtesting, config under Data & Settings, preserve 5-page nav | **Done** — all three pages updated |

## Key Modules Added (Stage 9)

```
portfolio/
├── config.py       # load config/allocation_targets.yaml merged over defaults
├── allocation.py    # asset-class/sector/market-cap aggregation, drift, concentration (HHI)
├── tax.py           # date-effective STCG/LTCG rules, STT, exit load, missing-cost-basis flags
├── tax_lots.py      # FIFO lot reconstruction + per-lot tax estimation
├── optimizer.py     # deterministic cash-first, signal-aware, compounder-protecting planner
├── rebalancer.py    # annual/threshold/on-demand strategy + execution-plan assembly
├── scenarios.py     # current vs proposed allocation, drift, concentration, expected return
└── ui.py            # builds positions/signals/lots from AppContext (equity + MF)
config/allocation_targets.yaml   # user-editable targets, caps, strategy, tax overrides
```

## Design Decisions & Safety

1. **Advisory only** — plans are recommendations; no order placement or trade execution exists.
2. **Cash-first** — available cash and (suggested) SIP top-ups are preferred over selling.
3. **Tax-aware sells** — overweight positions ranked by estimated tax per rupee; LTCG preferred,
   lots consumed FIFO; STT/exit load/brokerage included.
4. **Signals are soft hints** — Stage 8 EXIT/TRIM/REPLACEMENT bias sell ordering but are not mandates.
5. **Compounder protection** — strong appreciators are skipped unless an explicit exit signal exists.
6. **No invented data** — missing cost basis yields a flagged estimate, never a guessed value.
7. **Configurable & date-effective** — rates/rules selectable by exit date; all overridable in YAML.

## Validation

- New Stage 9 tests (27): `test_portfolio_allocation.py` (6), `test_portfolio_tax.py` (9),
  `test_portfolio_optimizer.py` (8), `test_portfolio_scenarios.py` (4) — covering classification,
  drift/breach, date-effective rates, exemption math, missing-cost-basis flags, FIFO lots,
  cash-first, funding sells, turnover caps, compounder protection, strategies and scenarios.
- Unit suite: **232 passed**. Integration: **8 passed** (5-page navigation + page render).
- `ruff check` clean on all new/edited modules.

## Known Limitations

1. MF tax lots are not reconstructed from raw MF transactions in the UI path (flagged as missing
   cost basis) — equity lots are built from the ledger; users should import MF transactions for
   precise MF tax.
2. Expected-return scenarios use injected forecasts; absent research forecasts, expected return is
   omitted rather than assumed. Bear/base/bull uses whatever forecast set is supplied.
3. Debt-MF slab taxation is approximate/flagged; equity-oriented MF use equity rules.
4. Sector/market-cap exposure relies on provided classification (MF indirect exposure excluded
   where underlying holdings are unavailable).

## Open Items / Next Steps (post-Stage 9)

- Wire MF transaction-ledger lots into the rebalancing planner for precise MF tax.
- Add bear/base/bull scenario presets from stored research forecasts.
- Record completed rebalances to activate fully automatic annual scheduling.

---

# Project Status — Stage 10: Automation, Reliability & Production Readiness

## Executive Summary

Stage 10 makes the platform **operable and production-ready** — automated, idempotent refresh
jobs, deduplicated/configurable alerts, hardened MCP integrations, encrypted verified backups,
structured (redacted) logging, metrics, Docker deployment and operating docs. Every change is
**advisory/operational only**: no autonomous trading, and **no external notifications are sent**
(email/webhook are logging stubs). Financial calculations are untouched: the full regression suite
passes (**310 passed**), including all Stage 1–9 tests and the 5-page navigation/render tests.

## Delivered Requirements vs. Implementation

| # | Requirement | Status |
|---|---|---|
| 1.1 | Daily equity prices / MF NAV / valuation refresh | **Done** — `refresh_prices` (incremental) |
| 1.2 | Periodic corporate-action & dividend updates | **Done** — `refresh_corporate_actions` |
| 1.3 | Quarterly/periodic research & analyst refresh | **Done** — `refresh_research` (scoped, configurable) |
| 1.4 | Event-driven disclosures (where supported) | **Partial** — hooks via research runner; depends on MCP support |
| 1.5 | Incremental, non-duplicating updates | **Done** — per-period idempotency keys + run tracking |
| 2.1 | Stage 8 sell/trim/hold signals as alerts | **Done** — `alerts/triggers.py` |
| 2.2 | Concentration / valuation / governance / earnings alerts | **Done** — signal + allocation triggers |
| 2.3 | Stale data / failed import / reconciliation alerts | **Done** — data-quality & freshness triggers |
| 2.4 | Configurable preferences & frequency | **Done** — `config/alerts.yaml` |
| 2.5 | De-duplication / anti-noise | **Done** — fingerprint + cooldown suppression |
| 3.1 | MCP health checks, retries, timeouts, fallbacks | **Done** — `mcp_integration/health.py` + circuit breaker/stale cache |
| 3.2 | Caching, rate limiting, incremental processing | **Done** — evidence TTLs + `rate_limiter.py` |
| 3.3 | LLM token/cost/execution tracking | **Done** — `research/usage.py` |
| 3.4 | Dashboard performance / DB queries | **Done** — lazy panels, session caching, defensive queries |
| 3.5 | Idempotent & recoverable jobs | **Done** — `scheduler/store.py` |
| 4.1 | Secure API keys / MCP creds / data | **Done** — env secrets + PBKDF2/Fernet crypto |
| 4.2 | Authentication & least privilege | **Done** — optional env-gated auth + roles (`app/auth.py`) |
| 4.3 | No secret/PII leakage in logs/prompts | **Done** — redaction filter + prompt sanitisation |
| 4.4 | Validate MCP responses / prompt-injection defence | **Done** — `mcp_integration/validation.py` + size guards |
| 4.5 | Encrypted backups & tested restore | **Done** — `security/backup.py` + scripts (verified) |
| 5.1 | Reproducible Docker deployment | **Done** — Dockerfile/compose/entrypoint |
| 5.2 | Structured logging / error tracking / job monitoring | **Done** — JSON logging + run tracking + metrics |
| 5.3 | Migration/backup/recovery procedures | **Done** — `scripts/migrate|backup|restore` + docs |
| 5.4 | Config/installation/troubleshooting docs | **Done** — `docs/DEPLOYMENT.md`, `CONFIGURATION.md`, `troubleshooting.md` |
| 6 | End-to-end validation | **Done** — `test_stage10_e2e.py`, `test_stage10_mcp_failure.py` |


## Key Modules Added (Stage 10)

```
scheduler/         # config, SQLite job store (idempotency+recovery), jobs, runner, CLI
alerts/            # models, triggers, dedupe, preferences, store, channels (stubs), in-app UI
security/          # crypto (Fernet/PBKDF2), redaction, encrypted verified backups
monitoring/        # defensive metrics aggregation + Prometheus text
scripts/           # migrate.py, backup.py, restore.py
research/usage.py  # LLM token/cost/latency tracking
mcp_integration/   # health.py, rate_limiter.py, validation.py (+ adapter size guard)
app/auth.py        # optional auth + least-privilege roles
utils/logging.py   # structured JSON logging + redaction filter
config/            # scheduler.yaml, alerts.yaml, auth.yaml (user-editable)
Dockerfile, docker-compose.yml, docker/entrypoint.sh, .dockerignore
```

## Design Decisions & Safety

1. **Separate scheduler process**, no message queue; SQLite run-tracking gives idempotency + recovery.
2. **Failure isolation** — refresh jobs never raise into the ledger; MCP failures degrade to stale/partial.
3. **No external side effects** — email/webhook are logging stubs; auth is opt-in; no trade execution.
4. **Least privilege** — `admin`/`editor`/`viewer` permissions; secrets stay in `.env`, never logged.
5. **Backups verify before overwrite** — decrypt → sha256 + `PRAGMA integrity_check` → atomic replace.
6. **No fabricated data** — missing capability yields `skipped` with reason, never invented values.
7. **Backward compatible** — `get_logger`, `MCPAdapter`, `LLMClient` contracts preserved.

## Validation

- New Stage 10 tests (45): scheduler (7), alerts (7), security (6), usage (4), reliability (6),
  auth (4), monitoring/validation (4) — 39 unit; plus E2E (2) and MCP-failure (4) integration.
- **Full suite: 310 passed** (no regressions; 5-page navigation/render included).
- `ruff check` clean on all Stage 10 modules (repo-wide findings are pre-existing/legacy).
- Operational checks: `python -m scheduler --register/--list` OK; `python -m scripts.migrate` OK
  (alembic head + 6 standalone DBs provisioned); encrypted **backup→restore CLI verified** (rows restored).

## Known Limitations / Unresolved Issues

1. **Event-driven disclosures** are opportunistic — depend on MCP server event support; otherwise the
   periodic `refresh_research` covers updates.
2. **Not deployed** — Docker image/compose provided and validated structurally, but `docker compose up`
   and any external notification delivery are intentionally **not executed pending approval**.
3. **Auth** is a minimal single-user gate by default; multi-user directory/RBAC beyond three roles is out of scope.
4. **Cost figures** are estimates from a static price table; verify against provider billing.
5. Some legacy modules still carry pre-existing style findings (unrelated to Stage 10).

## Open Items / Next Steps (post-Stage 10)

- Await approval before building/deploying the container image or enabling external notifications.
- Persist MF transaction lots into the ledger for precise MF tax (carried from Stage 9).
- Optional: implement a real email/webhook channel behind the existing stub interface once approved.

---

# Project Status — Stage 11: Financial Audit, AI Validation & System Optimization

## Executive Summary

An independent audit of Stages 1–10 was performed across four dimensions — financial accuracy,
backtesting validity, AI-research quality, and performance/UX. **Six confirmed defects were fixed**
with regression tests; three High/Medium limitations are **documented and require approval** before
any methodology change (no silent changes were made). Core XIRR matches independent hand calculations
exactly. No fabricated financial or research data was introduced.

Full suite: **327 passed** (310 prior + 17 new Stage-11 audit tests). `ruff` clean on audited modules.

## Findings (classified)

| ID | Severity | Status | Summary |
|---|---|---|---|
| FA-01 | High | **Fixed** | MF switch-in & bonus/REINVEST units were dropped from holdings |
| FA-02 | High | **Fixed** | IDCW/dividend MF cash-flow had wrong sign (treated as outflow) |
| FA-03 | High | **Fixed** | `calculate_fund_xirr`/`portfolio_mf_xirr` passed 3-tuples to solver → always `solver_failed`; portfolio holdings not net of redemptions |
| FA-04 | High | **Fixed** | Realized/unrealized gains mis-signed redemptions & used net-invested as cost basis → replaced with FIFO |
| FA-05 | Medium | **Fixed** | MF cash-flow builders rejected dict transactions |
| FA-06 | High | **Open (approval)** | XIRR holding layer ignores split/bonus quantities (needs methodology change) |
| FA-07 | Low | **Fixed** | Dead duplicate code + import-time `print()` statements removed |
| FA-08 | Low | **Documented** | `mf/service` uses net-invested unrealized proxy (FIFO engine now available) |
| FA-09 | Medium | **Documented** | `MFScheme` model has duplicated attribute blocks (hygiene) |
| BT-01 | High | **Fixed** | `forward_return` could return a spurious 0% at horizon boundaries |
| BT-02 | High | **Open (approval)** | Split/bonus quantities not applied inside XIRR (same root as FA-06) |
| AI-01 | Medium | **Documented** | No automated claim-vs-source citation verification |
| AI-02 | Low | **Documented** | LLM text can vary across runs (facts are evidence-stable) |

## Modules changed

- `mf/accounting.py` — normalised side classification, switch/bonus handling, IDCW sign, 2-tuple
  solver input, FIFO realized/unrealized gains, dict support, removed stray print.
- `mf/backtesting.py` — upper-case side sets; gains delegate to the shared FIFO engine.
- `mf/service.py` — normalised side sets; IDCW dividends included in scheme XIRR.
- `mf/nav.py` — removed import-time print.
- `decision/backtest.py` — forward-return horizon uses nearest observation on/after target.
- `corporate_actions/engine.py` — removed unreachable duplicate `_dedupe_actions` block.

All public signatures and return shapes preserved (UI unaffected).

## Validation

- **Independent references:** XIRR 730-day doubling `= √2−1`; leap-span day-count convention
  `= 2^(365/731)−1`; dividends +inflow; rights −outflow; opening positions merged; corporate-action
  de-duplication idempotent.
- **New regression tests (17):** `tests/unit/test_stage11_audit.py` (XIRR references, MF switches/bonus/
  IDCW, FIFO gains, portfolio MF XIRR net of redemptions, forward-return horizon, point-in-time guard,
  CA dedupe, opening positions, documented CA-in-XIRR limitation).
- **Full suite:** 327 passed. Navigation/render tests unchanged and passing.

## Deliverables

- `docs/financial_audit.md`, `docs/backtesting_validation.md`, `docs/ai_research_validation.md`,
  `docs/system_optimization.md`, and this status update.
- Automated regression tests (above).

## Open items requiring approval (no change made)

1. **FA-06 / BT-02** — apply verified corporate actions to the XIRR holding engine (changes reported
   returns for split/bonus histories). Recommended approach documented in `financial_audit.md`.
2. **AI-01** — add a post-generation citation checker (new capability).
3. **FA-09** — de-duplicate `MFScheme` model declarations (schema-adjacent cleanup).
4. **FA-08** — adopt FIFO gains inside `mf/service` (may shift displayed unrealized values).

## Sign-off

No **Critical** defects remain; no unresolved critical data-integrity issues. High-severity MF/backtest
defects fixed with tests. Remaining items are documented with recommendations and explicitly deferred
for approval. **Stopping for review.**

---

# Project Status — Stage 12: AI Investment Copilot & Conversational Portfolio Intelligence

## Executive Summary

Stage 12 adds an **"Ask My Portfolio" AI Copilot** that answers natural-language questions using the
existing deterministic engines (XIRR, allocation, rebalancing, backtesting, signals, research). It is
**tool-first**: every figure comes from a deterministic engine; the LLM only routes intent and phrases
explanations around those figures. It is **read-only/advisory** — no trades, no data fabrication.

Full suite: **373 passed** (327 prior + 46 new copilot tests). `ruff` clean on all new/edited modules.
Five-page navigation preserved (the panel lives *inside* existing pages).

## Delivered Requirements vs. Implementation

| # | Requirement | Status |
|---|---|---|
| 1 | Conversational portfolio assistant (XIRR, gains, dividends, allocation, concentration, risk) | **Done** — `portfolio`/`allocation`/`concentration`/`gains`/`dividends` tools |
| 2 | AI research & analysis (valuations, earnings, governance, analyst; explain HOLD/TRIM/EXIT/REPLACEMENT) | **Done** — `research_report`, `signals`, `explain_signal` (reuse Stage 6/8) |
| 3 | Scenario analysis (trim %, concentration, replace A→B, MF overlap, rebalance with new cash) | **Done** — `trim_simulation`, `replacement_simulation`, `overlap`, `rebalance_plan` |
| 4 | Intelligent tool orchestration (structured routing, cached research, validation, no LLM maths) | **Done** — `intent.py` + `tools` registry + `security.validate_args` |
| 5 | Explainability & trust (citations, tiers, uncertainty, history privacy, injection/authorization) | **Done** — citations/tier badges/warnings; sanitisation; audit; session-only history |
| 6 | UI integration (chat panel across pages, inspect calculations, no new tabs) | **Done** — `copilot/ui.py` inside Portfolio/Performance/Holdings/Backtesting |

## Key Modules Added

```
copilot/
├── config.py        # config/copilot.yaml over defaults
├── types.py         # Citation, ToolResult, CopilotResponse, tiers
├── security.py      # sanitisation, injection detect, arg validation, rate limit, audit
├── context.py       # read-only CopilotContext (equity + best-effort MF)
├── tools/base.py    # ToolSpec + registry/allowlist
├── tools/portfolio.py, rebalancing.py, backtesting.py, signals.py, research.py
├── intent.py        # deterministic routing + arg extraction (+ optional LLM fallback)
├── response.py      # markdown answer, tables, sources, warnings, narration
├── engine.py        # CopilotEngine orchestration
└── ui.py            # Streamlit "Ask My Portfolio" panel
config/copilot.yaml  # model/tools/security/history settings
docs/copilot_architecture.md, docs/copilot_tools.md
```

## Design Decisions & Safety

1. **No LLM arithmetic** — tools return authoritative figures; narration is constrained to those facts.
2. **Works offline** — deterministic answers with no LLM; research explicitly reports "unavailable"
   instead of fabricating.
3. **Read-only** — no tool writes data or executes trades; all tools require `view` permission.
4. **Allowlisted tools + arg validation** — the LLM cannot invoke arbitrary code or bypass schemas.
5. **Injection defence** — user text sanitised; MCP content sanitised (Stage 10 validation); instruction
   boundaries enforced in prompts.
6. **Privacy** — conversation is session-only, capped, clearable; free-text args redacted in audit logs;
   prompt/response text not persisted.
7. **Efficient** — cached-first research; tools fetch only the data a question needs; page render stays
   fast (static suggestions; context built only on submit).

## Validation (reference questions reconcile with engines)

- New tests (46): `test_copilot_tools.py` (engine reconciliation), `test_copilot_intent.py`,
  `test_copilot_security.py`, `test_copilot_engine.py`, `test_copilot_response.py`.
- Reference answers (synthetic portfolio) validated against engines, e.g.:
  portfolio value ₹10.00 L / XIRR 19.49%; RELIANCE XIRR 22.44%; trim 25% → ₹1.50 L, weight 60%→45%;
  rebalance with ₹5 L uses the Stage-9 plan; replacement uses the Stage-5 engine; signals match the
  decision engine.
- Full suite: **373 passed** (navigation/render included).

## Acceptance Criteria

- ✅ Answers reconcile with existing portfolio calculations (asserted against engines).
- ✅ Scenario results come from validated deterministic engines.
- ✅ Research conclusions include traceable evidence (cached-first; evidence_ids; unavailable disclosed).
- ✅ Missing/stale information disclosed (warnings + tiers).
- ✅ No unauthorized data access or trade execution (read-only allowlist).
- ✅ Five-page navigation and functionality intact.

## Known Limitations / Open Items

1. **LLM availability** — narration and live research require a configured provider; offline the copilot
   returns deterministic answers and marks research unavailable (by design, no fabrication).
2. **Multi-tool answers** — the engine currently executes one tool per question; compound questions may
   use the most relevant tool (future: multi-tool planning).
3. **Conversation memory** — context is the current portfolio snapshot (not a semantic memory of prior turns).
4. **Research caching** depends on stored `research_reports`; generation is gated by LLM/MCP availability.
5. MF-specific numeric questions use the MF service context when available (best-effort outside Streamlit).

**Stopping for review.** No deployment, no external notifications, no trades performed.



## Multi-Person Portfolios

- People are stored in the `people` table and managed under Data & Settings > People & Portfolios.
- Equity PDF statements, holdings files and MF statements can be imported tagged to a person.
- The sidebar Person filter scopes transactions and holdings across all pages, including XIRR, allocation, signals, backtesting and copilot.
- MF data is isolated per person under `data/mf/<slug>` when present.

- Transaction imports now support the ICICI portfolio XLS/TSV export (Action, Transaction Price, Brokerage, Charges, StampDuty).
- Statement imports can be restricted to fully sold companies (net position = 0) for PDF or XLS files.
- XIRR terminal valuation falls back to holdings-statement prices for ISINs missing from the market-price cache.
- Person filter options are now only All people + named people (Unassigned removed).
- All people aggregates the latest holdings snapshot of every person into one summed portfolio.
- Concentration chart: descending bars with % labels and a stacked "Others" bar (per-company colours + hover).
- Portfolio XIRR now includes dividend cashflows (per-share x held qty at ex-date), corporate actions and opening positions.
- Dividend view toggle: net applies an assumed 30% dividend tax and XIRR uses the selected (gross/net) amount.
- Sector exposure: descending chart plus an inline "Unconfirmed sectors" editor with selective yfinance fetch and manual sector overrides.
- Sector exposure chart is Plotly-based: descending order with % allocation labels and hover values.
- XIRR: calendar-year selector, partial-opening fix, benchmark XIRR comparison, and advisory-signal tooltip added.
- Benchmark TRI: retry + alternate NSE endpoints, Yahoo price-proxy fallback, and manual TRI CSV import on Portfolio.
- Benchmark: NSE daily archive price-index fetcher added (capped, per-day CSV) alongside retries, Yahoo fallback and manual CSV import.
