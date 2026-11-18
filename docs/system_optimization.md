# System Optimization & UX Review — Stage 11

**Scope:** slow queries, unnecessary MCP calls, excessive LLM usage, caching/processing efficiency,
dashboard responsiveness, and a usability review of the existing five-page navigation. Constraint:
**no new features**, preserve functionality and navigation.

**Result summary:** the platform is already lean for a local single-user app (SQLite, cached context,
evidence TTLs, idempotent scheduler). No blocking performance defects found. A few tuning
opportunities are documented; no behavioural changes were made (to preserve functionality). The
Stage-11 correctness fixes do not add cost (same O(n) passes).

---

## 1. Data access & query efficiency

| Area | Observation | Verdict |
|---|---|---|
| SQLite indexes | `transactions(isin, fingerprint, source_file)`, `mf_transactions(portfolio_id, scheme_code, trade_date)`, `mcp_cache(expires_at)`, scheduler/alerts/usage indexes present | ✅ adequate |
| Portfolio context load | `app/context.build_context` caches `txs`/`holdings` in `st.session_state` (loaded once per session) | ✅ |
| Rebalancing context | lots grouped by security once (Stage 9) → O(positions + tx) not O(P×T) | ✅ |
| MF accounting | single O(n) pass per scheme; FIFO gains O(n·lots) worst case | ✅ fine for typical portfolios |
| Alert/metrics panels | run heavy work only on explicit button click; status reads are cheap | ✅ |

**SO-01 — MF aggregation could be indexed for very large folios — Low (OPTIONAL)**
- `mf/service` groups transactions per scheme in one pass (`by_scheme`) — already efficient. For very
  large histories, a persisted per-scheme summary would help, but this is not needed at current scale.
- **Recommendation:** if portfolio size grows, precompute scheme summaries in the Stage-7 analytics DB.

---

## 2. MCP & LLM call efficiency

| Area | Control | Verdict |
|---|---|---|
| MCP calls | evidence cache with per-capability TTL + `args_hash` idempotency + circuit breaker | ✅ |
| Stage-9 hot path | rebalancing reuses `session_state["decision_recs"]`; **does not** recompute Stage-8 on page render (fixed during Stage 9) | ✅ |
| LLM usage | invoked only for EXIT/REPLACEMENT reviews when enabled; deterministic rules retain veto | ✅ |
| LLM cost visibility | per-call token/cost/latency via `UsageTracker` | ✅ |
| Scheduled refresh | idempotent per-period keys avoid duplicate upstream calls | ✅ |

**SO-02 — Prompt sizes are truncated per domain — Low (by design)** — orchestrator caps evidence
snippets (2000/1500 chars) to bound token usage; documented trade-off (may omit tail data).

---

## 3. Dashboard responsiveness

- Five pages remain: **Portfolio · Performance · Holdings & Research · Backtesting · Data & Settings**
  (`test_five_page_navigation` + `test_each_page_renders` pass in this stage's full run).
- Stage-9/10 additions live *inside* Portfolio (rebalancing insights, alert center) and *inside*
  Data & Settings (automation/monitoring), preserving navigation.
- Expensive context is built once; alert scan and scheduler/metrics actions are button-gated.

**SO-03 — Alert "Scan" performs a data-quality pass — Low (acceptable)** — the button calls
`reports.get_data_quality_summary` (opens the market-data store). It is user-initiated only, so it does
not affect normal page render; retained as-is.

---

## 4. UX review (five-page navigation)

| Page | Assessment | Note |
|---|---|---|
| 1 · Portfolio | Consolidated holdings, allocation, rebalancing insights, alerts | Dense but logically grouped; alert center adds value without new nav |
| 2 · Performance | XIRR/TWR, benchmarks, gains, dividends | Clear metrics; `estimated_xirr_note` flags provisional values |
| 3 · Holdings & Research | Unified table + AI research + decision signals | Evidence-linked reports; advisory only |
| 4 · Backtesting | Rebalancing simulator + stock-replacement | Clearly labelled assumptions; biases documented |
| 5 · Data & Settings | Imports, reconciliation, MCP, automation/backup | Operational actions separated from analytics |

No navigation changes were made (constraint honoured). Recommended future polish (not implemented):
per-section anchors on Portfolio, and a compact "data freshness" banner on Performance.

---

## 5. Optimizations applied / not applied

- **Applied (indirect):** Stage-11 correctness fixes remove silent-failure paths (MF XIRR previously
  always returned `None`, forcing recompute/fallback); FIFO gains computed once per scheme.
- **Not applied (to avoid functional change):** adopting the FIFO engine inside `mf/service`
  (`_scheme_valuation`) and adding DB summary tables. Both are safe future optimizations.
- **Verification:** full test suite completes in ~3.5 min (includes Streamlit page-render integration);
  unit suite ~5 s.

---

## 6. Sign-off

- No blocking performance issues; caching/idempotency controls are in place.
- Navigation and functionality preserved; no features added.
- Recommendations documented for future scaling; all Stage-11 changes are behaviour-neutral for
  performance except eliminating the MF-XIRR silent-failure path.
