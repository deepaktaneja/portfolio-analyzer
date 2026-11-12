# Financial Methodology — XIRR, Corporate Actions, and Backtesting

**Stage 0 deliverable.** Deterministic, auditable methodology for the portfolio intelligence application's financial calculations. All calculations are pure Python; LLMs interpret results but never compute them.

## 1. XIRR (Extended Internal Rate of Return)

### Engine
- Root-finding via `pyxirr` (irregular cash-flow IRR), wrapped with a fixed solver tolerance and iteration cap. Solver status/convergence is recorded per run.
- Sign convention: **outflows negative** (buys, SIP, MF purchases), **inflows positive** (sells, redemptions, dividends). Terminal portfolio market value is a positive flow at the as-of date.
- Dividends are included as positive flows on their payment date.
- Corporate actions adjust quantity/cost but are **not** cash flows unless they involve actual cash (rights subscription outlay, buyback proceeds).

### Scopes
| Scope | Cash flows included |
|---|---|
| Stock-level | All buys/sells/dividends for one ISIN, plus terminal market value of remaining units |
| Fund-level | All MF purchases/redemptions/dividends for one scheme/folio, plus terminal MF value |
| Consolidated portfolio | All equity + MF cash flows across the portfolio, plus total terminal value |

### Auditability
- Every XIRR run stores its exact cash-flow list in `xirr_cashflows` (run_id, scope, security/scheme, date, amount, label).
- Given the same `run_id`, the same cash flows produce the same XIRR → fully reproducible.
- LLM never computes XIRR; it only interprets the stored result.

## 2. Corporate actions (applied to FIFO lots)

Processing order: by `effective_date` (fallback `record_date`, then `ex_date`), respecting an `as_of_date` cutoff. An action effective after the valuation date is **not** applied → no look-ahead.

| Event | Lot adjustment | Cost-basis treatment | Verification |
|---|---|---|---|
| Split | qty × ratio | cost per lot ÷ ratio (total cost preserved) | auto |
| Bonus | qty × (1 + ratio) | cost spread across new total qty (per-unit cost ↓) | auto |
| Rights | new lots added at subscription price on allotment date; lapsed rights → no qty change | new lots at subscription cost | unverified flagged |
| Merger | lots remapped to `successor_id` by ratio | cost split per `cost_allocation` | **must be verified** |
| Demerger | lots split into parent + successor by ratio | cost split per `cost_allocation` | **must be verified** |
| Buyback | lots reduced (FIFO); proceeds treated as sell cash flow | remaining lot cost scaled down | auto |

- Unverified mergers/demergers are skipped during reconstruction and flagged for manual review — never silently applied.
- Verified/unverified status and review flags are persisted (`corporate_actions.verified`, `review_flags`).
- Deterministic ordering: ties broken by a stable sort key so identical inputs always yield identical lot states.

## 3. Realised / unrealised gains

- **Realised gains**: FIFO lot matching on SELL/REDEMPTION. `gain = proceeds − matched_cost`, summed per security/scheme and period.
- **Unrealised gains**: `market_value − remaining_cost_basis` using the latest known price as of the as-of date.
- Dividends tracked separately as income, not netted into cost basis.
- Stock-level, fund-level, and consolidated roll-ups are recomputed from the same lot engine for consistency.

## 4. Backtesting — replace historical stock with alternative

### Method
1. Take the actual historical cash flows for security A over [start_date, end_date].
2. Simulate investing the same amounts on the same dates into security B, using B's historical prices adjusted for B's corporate actions and dividends.
3. Apply a configurable transaction-cost model (brokerage + GST + charges) to every simulated buy/sell.
4. Optionally reinvest dividends or treat them as cash.
5. Compute: actual portfolio XIRR vs hypothetical portfolio XIRR; also benchmark-match both against Nifty 50/500 TRI using identical cash flows.

### Look-ahead bias guard
- All price, corporate-action, and dividend data used in the simulation must be known as of each trade date.
- Only verified corporate-action data is applied; unverified mergers/demergers are excluded and flagged.
- No future information leaks into past decisions.

### Outputs
- Stored in `backtest_runs` (actual_xirr, hypothetical_xirr, cost_basis, dates) and `xirr_cashflows` for audit.
- Comparison report shows cost impact of transactions and excess return vs benchmark.

## 5. Determinism and auditability rules

- Money stored as `Decimal` (SQLAlchemy `Numeric`) for exact cents-level precision; rounded only at presentation. Quantities use sufficient decimal places to represent MF units precisely.
- Fixed date-parsing formats; UTC stored, IST displayed.
- Every derived number (holding, gain, XIRR, signal) is traceable to a source transaction row and/or an MCP evidence envelope via foreign keys.
- LLM outputs are stored as text/signal recommendations; they never overwrite accounting tables.
