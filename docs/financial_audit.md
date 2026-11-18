# Financial Accuracy Audit — Stage 11

**Scope:** independent verification of Equity, Mutual-Fund and consolidated performance math
(XIRR, cash flows, dividends, corporate actions, switches, SIPs, gains, tax lots), plus
duplicate/missing/incorrectly-classified transaction handling.

**Method:** hand-computed reference cases (`src/xirr`), targeted probes against the live code,
and regression tests in `tests/unit/test_stage11_audit.py`. No financial inputs were fabricated;
missing data is reported as a gap, never assumed.

**Result summary:** the core XIRR solver reproduces independent references exactly. Six real
defects were found and **fixed** (with regression tests). One High limitation (corporate-action
quantities in the XIRR layer) is **documented and requires approval** before a methodology change.

---

## 1. Independent reference cases (solver correctness)

| Case | Reference (hand) | Implementation | Result |
|---|---|---|---|
| 1,000 → 2,000 over 730 days (2021-01-01→2023-01-01) | `√2−1 = 0.414214` | `compute_xirr` = `0.414214` | ✅ match |
| 1,000 → 2,000 over 731 days (leap span) | `2^(365/731)−1 = 0.413543` | `0.413543` | ✅ match (365-day convention) |
| Same-sign cash flows only | `None` (no IRR) | `None` | ✅ |
| Equity XIRR with a verified dividend | dividend is +inflow; XIRR rises | +inflow; XIRR rises | ✅ |
| Rights subscription | −outflow (cash paid) | −outflow | ✅ |
| Opening positions + later buy | qty = opening + buy | qty = 15 (10 + 5) | ✅ |
| Corporate-action de-dup | split applied twice == once | `20 == 20` | ✅ idempotent |

> Note: XIRR uses a 365-day year, so a 2-calendar-year span containing a leap day is not exactly
> `√2−1`; the implementation is correct. The apparent mismatch in the first probe was a reference
> error (leap day), now codified as a test.

---

## 2. Findings

Severity: **Critical** = corrupts headline numbers/integrity; **High** = materially wrong numbers
in common scenarios; **Medium** = wrong/limited in specific scenarios; **Low** = hygiene/robustness.

### FA-01 — MF switch-in and bonus units were dropped — **High (FIXED)**
- **Symptom:** `build_folio_units` returned no units for `SWITCH_IN` destination schemes nor for
  `BONUS`/`REINVEST`/`GROWTH`/`ACCUMULATION`/`ALLOTMENT` allotments (lower-case side strings compared
  against upper-cased sides; `SWITCH_IN` was an explicit `pass`). Probe: `{'A': 0}` (B=50, C=10 missing).
- **Root cause:** inconsistent case normalisation + unimplemented `SWITCH_IN` in `mf/accounting.py`.
- **Fix:** upper-case `BUY_SIDES`/`SELL_SIDES`; `SWITCH_IN`/`STP_IN`→buys, `STP_OUT`→sells.
  Modules: `mf/accounting.py`, `mf/backtesting.py`, `mf/service.py`.
- **Regression:** `test_mf_units_include_switch_in_and_bonus`, `test_mf_units_handle_lowercase_and_uppercase_sides`.

### FA-02 — IDCW/dividend cash-flow had the wrong sign — **High (FIXED)**
- **Symptom:** `build_fund_cashflows` recorded IDCW as `-amount` (outflow), treating a dividend
  receipt as extra investment and understating XIRR.
- **Fix:** IDCW/DIVIDEND now a **positive inflow**; added `DIVIDEND_SIDES`.
  Regression: `test_mf_cashflows_idcw_positive_and_switch_semantics`.

### FA-03 — MF XIRR always failed; portfolio holdings not net of redemptions — **High (FIXED)**
- **Symptom:** `calculate_fund_xirr`/`calculate_portfolio_mf_xirr` returned `solver_failed` for any
  real input because they passed **3-tuples** `(date, amount, label)` to `compute_xirr` (which expects
  2-tuples and silently dropped every row). The portfolio holdings loop also only added buys and
  never subtracted redemptions/switches, overstating terminal value.
- **Fix:** strip labels before solving; rebuild holdings via `build_folio_units`.
  Regression: `test_portfolio_mf_xirr_net_of_redemptions`.

### FA-04 — Realized/unrealized gains used wrong sign and cost basis — **High (FIXED)**
- **Symptom:** a full exit with +₹500 profit reported `realized_gain = −2500` (redemption stored
  negative; "realized" computed as `redeemed − invested`).
- **Fix:** FIFO cost-basis engine — `realized = Σ(proceeds − FIFO cost sold)`,
  `unrealized = current value − FIFO cost remaining`; shared by accounting & backtesting.
  Regressions: `test_fifo_realized_gain_full_exit`, `test_fifo_partial_exit_cost_basis`,

### FA-05 — MF cash-flow builders rejected dict transactions — **Medium (FIXED)**
- **Symptom:** `build_fund_cashflows` used attribute access only; dict transactions raised
  `AttributeError` though sibling functions accept dicts.
- **Fix:** field accessor accepts objects **and** dicts. Regression: `test_mf_cashflows_accept_dict_transactions`.

### FA-06 — XIRR layer ignores splits/bonuses in holdings — **High (DOCUMENTED — needs approval)**
- **Symptom:** `performance/xirr._holdings_as_of` (and thus `calculate_stock/portfolio_xirr` opening
  & terminal values) applies only BUY/SELL; bonus/split quantity adjustments from
  `corporate_actions.engine` are **not** reflected. Probe: CA-aware qty `20` vs XIRR-layer `10`
  after a 2:1 split → terminal value wrong for split/bonus histories.
- **Affected:** `performance/xirr.py`.
- **Why not auto-fixed:** integrating corporate-action reconstruction into the XIRR holding engine
  changes reported returns for any split/bonus history — a **methodology change** requiring approval.
- **Recommended remediation (pending approval):** pass verified `actions` into `_holdings_as_of` and
  use `corporate_actions.engine.reconstruct_with_actions(...)` for opening/terminal quantities;
  restrict to `verified` actions and add golden tests.
- **Tracked by:** `test_documented_limitation_xirr_ignores_ca_quantities`.

### FA-07 — Dead code and import-time prints — **Low (FIXED)**
- Duplicate unreachable block in `corporate_actions/engine._dedupe_actions`; stray
  `print("mf/accounting.py created")` and `print("mf/nav.py created")` at import time.
- **Fix:** removed; no behaviour change.

### FA-08 — `mf/service._scheme_valuation` unrealized proxy — **Low (DOCUMENTED)**
- Uses `current_value − max(0, invested − redeemed)` (net-invested proxy) rather than FIFO cost.
  Left unchanged to avoid altering displayed UI values without approval; IDCW is now included in
  scheme XIRR. `mf/accounting` now exposes a correct FIFO engine that the service can adopt later.

### FA-09 — `MFScheme` model has duplicated attribute blocks — **Medium (DOCUMENTED)**
- `models/mf.py` re-declares `scheme_name/service/provider/...` and `__table_args__` several times in
  one class body (last-wins; functionally harmless but fragile). Recommend a dedicated de-duplication
  (deferred to avoid schema/mapper risk; no financial impact).

---

## 3. Duplicate / missing / misclassified transactions

| Check | Result |
|---|---|
| Import fingerprint dedup (equity & MF `fingerprint`) | ✅ present; covered by MF/data + Stage-10 tests |
| Corporate-action duplicate application | ✅ idempotent (`test_corporate_action_dedupe_idempotent`) |
| Negative-holdings detection | ✅ `ledger.positions.check_negative_holdings` |
| Opening holdings (synthetic 1-Jan purchase date) | ✅ merged in reconstruction (`test_opening_positions_merged_in_reconstruction`); opening-position tests pass |
| Unresolved identifiers / missing periods | ✅ surfaced by `reports.exports.get_data_quality_summary` → alerts |
| Unclassified sides (e.g. `STP`) | ⚠️ `STP` without direction is treated as no-op; statement-specific STP-IN/STP-OUT are handled. Documented limitation. |

---

## 4. Tax lots

- **Equity FIFO:** `portfolio/tax_lots.build_fifo_lots` consumes lots FIFO and flags missing cost
  basis (never assumes) — verified in Stage 9 tests and Stage 11 probe.
- **MF FIFO realized/unrealized:** now correct (FA-04). MF **tax** lots are still not wired into the
  rebalancing planner (carried limitation from Stage 9) — flagged as missing cost basis there.

---

## 5. Residual risk & sign-off

- No **Critical** defects remain.
- **High:** FA-01/02/03/04 **fixed**; **FA-06 open** (documented; approval required for the
  methodology change to apply corporate actions inside XIRR).
- All fixes preserve public function signatures and return-dict shapes (UI unaffected).
- Full suite: **327 passed**, including 17 new Stage-11 audit tests.

  `test_mf_backtesting_gains_delegate_matches_accounting`.