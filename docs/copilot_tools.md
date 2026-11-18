# Copilot Tools & Reference Q&A — Stage 12

All tools are read-only and wrap an existing deterministic engine. `args` are validated by
`copilot.security.validate_args` before execution.

| Tool | Question examples | Engine | Args |
|---|---|---|---|
| `portfolio_summary` | "What is my portfolio value and XIRR?" | `performance.xirr`, `portfolio.allocation` | — |
| `holdings` | "Show my top holdings" | holdings snapshot | `limit` |
| `xirr` | "What is the XIRR of RELIANCE?" / "What's my XIRR?" | `performance.xirr.calculate_stock_xirr / calculate_portfolio_xirr` | `security?` |
| `allocation` | "Show my asset allocation" | `portfolio.allocation.allocation_report` | — |
| `concentration` | "Which holdings drive concentration?" | `portfolio.allocation.concentration_metrics` | — |
| `gains` | "Show my unrealized gains" | holdings (value − qty×avg) | — |
| `dividends` | "Show my dividends" | `market_data.repository.get_dividend_events` | `security?` |
| `rebalance_plan` | "How can I rebalance with ₹5 lakh?" | `portfolio.rebalancer.build_execution_plan` → `portfolio.optimizer.plan_rebalance` | `cash` |
| `trim_simulation` | "What if I trim 25% of RELIANCE?" | `portfolio.allocation` (before/after) | `security`, `pct` |
| `overlap` | "Do my mutual funds overlap?" | `mf.overlap.overlap_matrix` (via MF service) | — |
| `replacement_simulation` | "What if I replace RELIANCE with TCS?" | `mf.backtesting.simulate_stock_replacement` + `calculate_backtest_xirr` | `security_a`, `security_b`, `full?` |
| `signals` | "Show my signals" | `decision.engine.DecisionEngine` | — |
| `explain_signal` | "Why TRIM on RELIANCE?" | `decision.engine` recommendation + signals | `security` |
| `research_report` | "Research RELIANCE" | `research.orchestrator` (cached-first) | `security` |

## Reference portfolio (validation fixtures)

Synthetic portfolio used by the regression tests (`tests/unit/copilot_helpers.py`):
- RELIANCE (INE001): 100 units, avg ₹4,000, price ₹6,000 → ₹6,00,000 (60%)
- TCS (INE002): 100 units, avg ₹3,000, price ₹4,000 → ₹4,00,000 (40%)
- Buys on 2023-01-01 (₹4,00,000 + ₹3,00,000); valuation date 2025-01-01.

## Reference questions → validated answers

| Question | Routed tool | Deterministic answer (reconciles with engine) |
|---|---|---|
| "What is my portfolio value and XIRR?" | `portfolio_summary` | Value ₹10.00 L; invested ₹7.00 L; gains ₹3.00 L; XIRR 19.49% |
| "What is the XIRR of RELIANCE?" | `xirr` | 22.44% |
| "Show my asset allocation" | `allocation` | Equity 100.0% vs target 65%; Debt 0%; Gold 0%; Alternatives 0% |
| "Which holdings drive concentration?" | `concentration` | HHI 0.52; largest holding 60.0% of 2 positions |
| "What if I trim 25% of RELIANCE?" | `trim_simulation` | Sells ₹1.50 L; weight 60.0% → 45.0%; HHI 0.52 → 0.5017 |
| "How can I rebalance with ₹5 lakh?" | `rebalance_plan` | Cash-first plan; sells/buys + estimated tax from Stage-9 engine |
| "Show my signals" | `signals` | HOLD/MONITOR/TRIM/EXIT counts from the decision engine |
| "Why trim RELIANCE?" | `explain_signal` | Action, confidence, triggering signal types + evidence |
| "What if I replace RELIANCE with TCS?" | `replacement_simulation` | Actual vs hypothetical XIRR/terminal from Stage-5 engine |
| "Research RELIANCE" | `research_report` | Cached report, or explicit "unavailable" if no LLM (never fabricated) |

These are asserted in `tests/unit/test_copilot_tools.py` and `tests/unit/test_copilot_engine.py`
(the copilot's numbers equal the engine's numbers).

## Disclosures

- Answers carry a **tier badge**: 🟢 Verified (deterministic engine) / 🟡 Estimated / 🟣 AI interpretation.
- Each answer lists **Sources** (engine + timestamp) and **Missing data / assumptions** warnings.
- Research answers are marked 🟣 AI interpretation; absence of an LLM yields an explicit unavailable
  message (no data invented).
- All answers end with **"Advisory only — no trades are executed."**
