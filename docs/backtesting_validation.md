# Backtesting Validation — Stage 11

**Scope:** historical stock-replacement simulation (`mf/backtesting.py`), the point-in-time signal
backtester (`decision/backtest.py`), corporate-action adjustments, and the bias/execution
assumptions underpinning both. Validated with hand-computed references and the Stage-11 audit tests
(`tests/unit/test_stage11_audit.py`, `test_backtesting.py`, `test_decision_backtest_storage.py`).

**Result summary:** one real defect fixed (BT-01, forward-return horizon selection). The
point-in-time guard is verified sound. Remaining biases are inherent to available data and are
documented here rather than silently "fixed" (no fabricated prices or estimates).

---

## 1. Stock-replacement simulation

`simulate_stock_replacement(txs, A, B, replacement_date, is_full_replacement)` swaps A's purchases
for the **same amounts on the same dates** in B, preserving all other transactions.

| Check | Method | Result |
|---|---|---|
| Preserves unaffected holdings | `test_simulate_stock_replacement_full/partial` | ✅ counts correct |
| Same investment amounts & dates | replacement reuses each original amount/date | ✅ (tested) |
| Actual vs hypothetical XIRR | `compare_portfolios` on identical inputs → zero difference | ✅ `test_compare_portfolios_same_stock` |
| Terminal wealth | `calculate_terminal_value` = qty × nav | ✅ `test_calculate_terminal_value` |
| Corporate-action adjustment | CAs applied in ledger/`corporate_actions`; simulation inherits adjusted quantities | ⚠️ see BT-02 |

**Execution assumptions (documented):** trades execute at recorded price/date; no intraday slippage
beyond explicit cost assumptions; replacement uses A's historical dates (B's price on those dates is
required — missing price ⇒ `missing_data` flag, never fabricated).

---

## 2. Signal backtester (HOLD / MONITOR / TRIM / EXIT)

`decision/backtest.evaluate_signals` measures forward returns from each dated signal against
**subsequent** prices only.

### BT-01 — Forward-return horizon could return a spurious 0% — **High (FIXED)**
- **Symptom:** `forward_return(series, from_date, days)` selected the last bar **on/before** the
  horizon. When the horizon fell just short of the next bar (e.g. 90 days with quarterly bars), it
  returned the *starting* price → `0.0`, corrupting hit/false-positive rates.
- **Fix:** use the nearest observation **on/after** the horizon (standard convention), falling back
  to the last on/before; require the end date to be strictly after the signal date.
- **Reference:** `[(2024-01-01,100),(2024-04-01,110)]`, 90-day horizon → `+10%` (previously `0%`).
  Tests: `test_forward_return_uses_nearest_observation_after_horizon`; existing
  `test_forward_return` (95-day case) still `−10%`.

### Look-ahead guard — **verified sound**
- `price_as_of(series, as_of)` takes the latest bar ≤ `as_of` and **never** a future bar
  (`test_backtest_point_in_time_never_uses_future_bar`, `test_price_as_of_point_in_time`).
- `filter_signals_until(events, as_of)` enforces point-in-time signal selection
  (`test_filter_signals_until_guard`).
- Signal *generation* must use only data known at the signal date; historical events must be
  regenerated from point-in-time snapshots (see Section 4).

### Metrics semantics (documented)
- `hit_rate`: fraction of sell-type signals with negative 3-month forward return.
- `false_positive_rate`: fraction of sell signals with > `false_positive_pct` (default 10%) 6-month return.
- `missed_upside_rate`: fraction with > `missed_upside_pct` (default 20%) 6-month return.
- `signal_value_proxy`: mean `(−3m return − round-trip cost)` per sell signal — a **transparent
  proxy**, not realised P&L (no sizing, no tax).

---

## 3. Biases & limitations (documented, not auto-fixed)

| Bias / assumption | Status | Mitigation / note |
|---|---|---|
| **Look-ahead bias** | Guarded | `price_as_of`/`filter_signals_until` restrict to ≤ date; tested. |
| **Survivorship bias** | Present if universe excludes delisted names | Backtester evaluates only supplied symbols; no invented universe. Documented data limitation. |
| **Execution realism** | Simplified | Fills at recorded/close prices; explicit round-trip cost (`BUY_HOLD_COST_PCT = 0.15%`); no slippage/liquidity model. |
| **Taxes** | Not in signal P&L | Signal backtest is pre-tax; tax-aware rebalancing lives in `portfolio/` (Stage 9). |
| **Corporate actions in XIRR** | ⚠️ BT-02 | Split/bonus quantity not applied in `performance.xirr` holding layer. |
| **Short/coarse history** | Present | Incomplete forward prices → signal `unvalidatable`, never zero-filled. |
| **Hindsight in replacement picks** | Present | Replacement choice is a user what-if, not an automated strategy claim. |

### BT-02 — Split/bonus quantities not applied inside XIRR (shared with FA-06) — **High (DOCUMENTED — needs approval)**
- Terminal/opening values in `performance/xirr._holdings_as_of` ignore bonus/split adjustments, so
  actual-vs-hypothetical XIRR for split histories is affected. Same root cause as `financial_audit.md`
  FA-06; remediation requires feeding the corporate-action engine into the XIRR holding layer
  (methodology change → approval).

---

## 4. Reference-scenario comparison & reproducibility

- All functions are **deterministic** (pure Python, sorted inputs, fixed solver tolerance); repeated
  runs yield identical outputs (`test_run_backtest_wrapper`).
- Signal evaluation requires historical price series + dated signal events; results match an
  independent reference when inputs match. Insufficient inputs return explicit
  `unvalidatable`/`None` reasons instead of estimates.
- Historical signal **regeneration** requires point-in-time research snapshots (Stage-8 open item);
  without them, only stored signal events can be evaluated.

---

## 5. Sign-off

- One defect fixed (BT-01) with regression tests; point-in-time guards verified.
- Remaining limitations are data-driven and documented; no fabricated data introduced.
- Full suite: **327 passed**.

- `buy_hold_return`: equal-weight first→last across symbols that have data.