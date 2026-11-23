# Strategy Logic — Stage 4 Direct Equity Engine

*Reverse-engineered directly from the source code. Every rule below is as written in performance/stage4_signals.py (signal generation) and performance/stage4_backtest.py (walk-forward execution). No rule is invented; anything not in code is not here.*

---

## 1. Market & Data Setup

**Source files:** performance/backtest_data.py (Stage 4E-1), market_data/repository.py, market_data/prices.py.

**Instrument universe** (`load_backtest_universe`): built from three sources —
1. Active equity holdings (net quantity > 0).
2. Saved watchlist stocks (`watchlist_stocks` table, persisted incubation watchlist).
3. Closed historical equity positions (net quantity == 0, e.g. YESBANK.NS).

**Ticker handling:**
- NSE yfinance-style tickers (`^` prefix kept; non-`^` names get a `.NS` suffix).
- EXCLUDED_TICKERS = {"MUFNDF"} (muted / non-equity names).
- Closed holdings stored as company names are mapped to canonical tickers via COMPANY_NAME_TO_TICKER (e.g. ICICI BANK LTD -> ICICIBANK.NS).
- Benchmarks: Nifty 50 ^NSEI (ETF fallback NIFTYBEES.NS), Nifty 500 ^CRSLDX (ETF fallback MONIFTY500.NS).

**Timeframe / data:**
- Daily Adjusted Close only; no intraday, no resampling.
- Bulk fetch via yfinance.download(period="8y", group_by="ticker") (FETCH_PERIOD = "8y"), cached in the local stock_prices table (adjusted_close preferred, close_price fallback). Subsequent runs read the cache with zero API calls.
- Delisted tickers (empty download) are skipped and recorded in skipped_tickers.
- IPO gating: a stock only enters the simulation once it has a real first buy date and >= IPO_MIN_TRADING_DAYS (200) trading days of post-listing history (is_ipo_ready).
- Warm-up: MIN_TRADING_DAYS = 260 trading days before signals are trusted.
- Benchmark TRI: price index accrues +1.30% p.a. dividend yield compounded daily as a Total-Return proxy.

---

## 2. Indicators & Signals

**Source file:** performance/stage4_signals.py (analyzeStockSignals, evaluate_signal, compute_sip_multiplier).

### Core metrics (analyzeStockSignals)

| Metric | Definition |
|---|---|
| aer | **Allocation Expansion Ratio** = current_weight / cost_weight = (market_value / total_market_value) / (invested_cost / total_invested_cost) = (market_value / invested_cost) / (total_market_value / total_invested_cost). Defaults to 1.0 when stock invested cost, total invested cost, or total market value is <= 0. |
| xirr | Stock XIRR from real buy dates to as_of_date. |
| cmp | Current market price (latest adjusted close). |
| dma_50 | 50-day simple moving average. |
| dma_200 | 200-day simple moving average. |
| annual_volatility | Std-dev of daily log returns over VOL_WINDOW = 252, annualised by 252. |
| dma200_distance_pct | (CMP - 200-DMA) / 200-DMA. |
| peak_price | Running high-water close over the holding window (used for trailing stops). |
| incubation | True when the stock is incubating (see Section 3). |
| gtt_stop_loss_price | Dynamic GTT stop level (trailing floor from peak). |

### Indicator constants (stage4_signals.py)

| Constant | Value | Meaning |
|---|---|---|
| MIN_TRADING_DAYS | 260 | Min history before signals trusted |
| DMA_SHORT / DMA_LONG | 50 / 200 | Moving-average lookbacks |
| VOL_WINDOW | 252 | Volatility lookback (trading days) |
| TRADING_DAYS_PER_YEAR | 252 | Annualisation factor |
| STOP_LOSS_FLOOR / STOP_LOSS_CAP | 0.20 / 0.45 | Dynamic stop % clamps |
| STOP_LOSS_MULT | 1.5 | Stop width = 1.5 x annual vol |
| TRAILING_PEAK_MULT | 2.00 | Trailing floor activates only after +100% rally |
| TRAILING_STOP_MULT | 1.50 | Trailing width = 1.50 x annual vol |
| TRAILING_STOP_FLOOR / _CAP | 0.35 / 0.45 | Trailing stop % clamps |
| INCUBATION_GTT_MULT | 0.60 | Incubation GTT = 40% below avg buy |
| GTT_BUFFER_WARN | 0.10 | Warn within 10% of GTT |

### Signal identifiers and priority (evaluate_signal)

HARD_STOP_LOSS, CONCENTRATION_TRIM, LAGGARD_EXIT, NO_SIGNAL ("NONE"). Rules are evaluated in strict priority order, first match wins:

1. **HARD_STOP_LOSS** (Priority 1)
2. **INCUBATION** (Priority 2 - sets sip_multiplier = 0.0, not a sell)
3. **CONCENTRATION_TRIM** (Priority 3)
4. **LAGGARD_EXIT** (Priority 4)
5. **NO_SIGNAL** (hold)

### Monthly-SIP lane constants

| Constant | Value | Rule |
|---|---|---|
| SIP_RUN_UP_DMA_DISTANCE | 0.30 | > +30% above 200-DMA -> Lane 3 freeze |
| SIP_PULLBACK_DMA_DISTANCE_MAX | 0.15 | Lane 1 2.5x only while 0..+15% |
| SIP_PULLBACK_AER_MAX | 1.30 | Lane 1 requires AER < 1.30 |
| SIP_LANE1_MULT | 2.50 | Lane 1 multiplier |
| SIP_PULLBACK_WINNER_MARGIN | 0.10 | 2.0x requires XIRR > benchmark + 10% |
| SIP_BROKEN_TREND_AER | 0.85 | AER < 0.85 -> broken-trend 0.0x |
| SIP_OVERALLOC_CAP_125_PCT | 1.25 | AER >= 1.26 -> pause SIP (0.0x) |
| MAX_SINGLE_STOCK_WEIGHT_PCT | 0.08 | Over-allocated freeze target (8% weight) |
| SIP_GRANULARITY | 5000.0 | Round SIPs to multiples of Rs 5,000 |

---

## 3. Buy (Entry) Conditions

**Source:** compute_sip_multiplier in performance/stage4_signals.py; executed monthly in _monthly_step in performance/stage4_backtest.py.

**Cadence:** monthly (each trading day d in the walk-forward loop; SIP buys fire on month boundaries). Every active holding is evaluated via evaluate_signals -> sip_multiplier, then compute_bottom_up_sip_allocation sizes the buys.

### Step 1 - Incubation gating (evaluate_signal, Priority 2)
A stock is **incubating** if ALL of:
- invested_cost < INCUBATION_MAX_INVESTED (100000.0), AND
- holding_days <= INCUBATION_MAX_DAYS (180), AND
- holding_days <= INCUBATION_MAX_AGE_DAYS (365) — i.e. held under 1 year.

If incubating:
- sip_multiplier is set by the incubation lane logic (1.0x healthy starter; 0.5x below-200-DMA or >+30% run-up).
- GTT stop set at INCUBATION_GTT_MULT = 0.60 x avg buy price (40% catastrophe circuit breaker).
- A stock held > 1 year (365+ days) can NEVER re-enter incubation, even if partial sells drop remaining cost below 100000.0.

### Step 2 - SIP lane assignment (compute_sip_multiplier)
For non-incubating holdings, the multiplier is a function of AER, XIRR and dma200_distance_pct:

- **Lane 1 - Conviction double-down (2.50x):** AER < SIP_PULLBACK_AER_MAX (1.30) AND 0% <= dma200_distance_pct <= +15% AND XIRR > benchmark + 10%.
- **Lane 2 - Healthy cruising (1.0x):** not over-allocated, not broken trend, not run-up frozen.
- **Lane 2b - Starter (0.5x):** dma200_distance_pct > SIP_RUN_UP_DMA_DISTANCE (+30% run-up) — incubating only; non-incubating below-200-DMA stocks get 0.0x via Lane 4.
- **Lane 3 - Run-up freeze (0.0x):** dma200_distance_pct > SIP_RUN_UP_DMA_DISTANCE (30%).
- **Lane 4 - Broken trend (0.0x):** dma200_distance_pct < 0 (below 200-DMA) OR AER < SIP_BROKEN_TREND_AER (0.85). Non-incubating holdings below 200-DMA already pause SIP at 0.0x; incubating holdings below 200-DMA also pause at 0.0x.
- **Over-allocation pause (0.0x):** AER >= SIP_OVERALLOC_CAP_125_PCT (1.25) pauses SIP until AER < 1.24.

### Step 3 - Monthly capital deployment (_monthly_step)
1. evaluate_signals runs on all active holdings -> sip_multiplier per stock.
2. compute_bottom_up_sip_allocation is called with max_single_stock_sip = budget (single-stock cap enforced by downscale + leftover redistribution).
3. Base unit SIP scaled: scaled_base = max(config.base_unit_sip, budget / weight_sum) so qualifying stocks can absorb up to the full budget.
4. For each eligible stock (multiplier > 0, not permanent_exited):
   - **Golden-cross reclaim guard:** if in cooldown after LAGGARD_EXIT, require CMP > 200-DMA AND 50-DMA > 200-DMA to re-enter; otherwise blocked for COOLDOWN_DAYS (90).
   - Cash guardrail: max_amt = cash / (1 + FRICTION_RATE); amt = min(amt, max_amt) — never allows negative cash.
   - Execute via _execute_buy at current price, with 0.25% friction deducted.
5. Any undeployed cash stays parked as plain cash (0.0% yield).

---

## 4. Sell (Exit) Conditions

**Source:** evaluate_signal (stage4_signals.py, Priority 1-4) plus execution gates _intraday_gtt / _monthly_step in stage4_backtest.py.

### Priority order
1. HARD_STOP_LOSS (Priority 1)
2. INCUBATION GTT (Priority 2 - not a sell; pauses SIP only)
3. CONCENTRATION_TRIM (Priority 3)
4. LAGGARD_EXIT (Priority 4)
5. NO_SIGNAL / hold

### 4.1 HARD_STOP_LOSS
Trigger conditions (in evaluate_signal + _monthly_step):
- Abs(holding return) stop breached: CMP <= avg_buy_price * gtt_stop_loss_price, OR AER <= HARD_STOP_AER_FLOOR (0.50), with invested_cost >= HARD_STOP_MIN_INVESTED (150000.0).
- Engine refinement: requires CMP < 200-DMA AND the stock drawdown exceeds Nifty 500 drawdown by >= 25 percentage points (index-relative), so index-wide crashes do not hard-stop a stock.
- Effect: full liquidation (fraction = 1.0); permanent_exited = True and frozen = True — never re-entered for the rest of the simulation.

### 4.2 CONCENTRATION_TRIM (Priority 3)
Trigger conditions (all required):
- AER >= TRIM_AER (2.20).
- market_value >= TRIM_MIN_MARKET_VALUE (200000.0).
- xirr >= TRIM_MIN_XIRR (0.25).
Effect: partial sell sized to bring the position back to TRIM_TARGET_MULT = 1.40 x the concentration target (fraction = min(1.0, shares / qty)).

### 4.3 LAGGARD_EXIT (Priority 4, engine-refined)
Original signal: evaluate_signal flags laggards held over 1 year with sip_multiplier = 0.0.
Engine execution gate (_laggard_exit_allowed) — all required:
- Held >= LAGGARD_EXIT_MIN_HELD_DAYS (730 calendar days = 2 full years).
- CMP < 200-DMA.
- Stock underperformed Nifty 500 by >= LAGGARD_EXIT_MIN_NIFTY_LAG (15pp) over the holding period.
Effect: full liquidation (fraction = 1.0); cooldown_until = d + COOLDOWN_DAYS (90); frozen = True. Re-entry only after the cooldown AND a confirmed golden cross (CMP > 200-DMA AND 50-DMA > 200-DMA).

### 4.4 GTT_STOP_LOSS (dynamic trailing stop, engine-refined)
Signal: CMP <= st.gtt (the signal-based dynamic GTT stop price from stage4_signals).
Engine execution gate (_gtt_stop_allowed) — all required:
- Stock drawdown from peak exceeds Nifty 500 drawdown over the same window by >= GTT_STOP_LOSS_MIN_NIFTY_LAG (20pp).
- If Nifty 500 is unmeasurable, hold (no blind stop).
Effect: full liquidation at the review-day market price (CMP); cooldown_until = d + 90; frozen = True.

Execution cadence (Stage 4 Exp #6, **KEEP**): GTT_STOP_LOSS is evaluated monthly-only, inside `_monthly_step`, on the review day. The exit sells at the review-day market price (CMP = cmp), not at the historical GTT trigger price; it runs after the hard-stop / trim / laggard exits and before the bottom-up SIP buys, so freed proceeds redeploy in-month. `_intraday_gtt` is peak-tracking only (no GTT sell).

### 4.5 CATASTROPHE_GTT (45% blind intraday floor) — DISABLED
The 45%-below-peak auto-liquidation was removed from _intraday_gtt. It previously set permanent_exited = True / frozen = True. CATASTROPHE_GTT is retained only in SELL_REASON_ORDER so the audit table shows an explicit zero row.

### 4.6 Partial sells and FIFO tax
- _execute_sell matches lots FIFO.
- STCG 20% when held < LTCG_HOLD_DAYS (365); LTCG 12.5% above the LTCG_EXEMPTION (125000.0) annual allowance when held >= 365 days.
- Friction FRICTION_RATE = 0.0025 (0.25%) on every buy and sell.
- Gross proceeds are recorded before friction/tax for audit reconciliation.

### 4.7 Time-based exits
- No calendar time-stop. Time only matters through: LAGGARD_EXIT 730-day minimum, cooldown 90 days, LTCG 365-day boundary, and the >1-year rule that blocks re-incubation.

### 4.8 Market Crash Freeze (Exp #8, KEEP)
- **Constant:** `CRASH_FREEZE_NIFTY_DD_THRESHOLD = -0.20` in `performance/stage4_backtest.py` (Nifty 500 trailing-52w-high drawdown < -20% → crash).
- **Diagnostics:** `_nifty500_52w_drawdown(prices, d)` computes the trailing-52w-high drawdown of `^CRSLDX` / `MONIFTY500.NS` (negative fraction; `None` when the benchmark is unmeasurable).
- **Behaviour:** During a monthly review month where the crash freeze engages, the defensive sells `GTT_STOP_LOSS` and `LAGGARD_EXIT` are paused (hold through the crash month) so index-driven risk reduction does not add to drawdown during a crash.
- **Untouched:** `HARD_STOP_LOSS`, `CONCENTRATION_TRIM`, and SIP buys are unchanged. No freeze when the benchmark data is unavailable.

---

## 5. Position Sizing & Risk Management

**Source:** compute_bottom_up_sip_allocation, compute_stock_natural_sip, _monthly_step.

### Capital allocation
- Monthly fresh capital = max(0, real-ledger net buy inflow) from _monthly_net_buy_inflow. Net-sell months inject nothing (no artificial benchmark sell).
- max_monthly_ceiling governs only the fresh monthly cash injection into `cash`; it does NOT cap redeployment of recycled sell proceeds, which deploy up to the friction-capped available cash (cash / (1 + FRICTION_RATE)).
- Natural SIP per holding = lane_multiplier x base_unit_sip, floored to SIP_GRANULARITY (Rs 5,000) and capped by MAX_SINGLE_STOCK_SIP.

### Single-stock cap and sizing
- The deployable budget is the full available liquid pool (fresh capital + recycled sell proceeds), capped only by friction (cash / (1 + FRICTION_RATE)); the allocator downscales proportionally when the natural total exceeds this budget, then redistributes leftover up to the per-stock cap.
- No fractional shares. Quantities come from FIFO lots; buys convert rupees to whole shares at the current price.

### Circuit breakers
- HARD_STOP_LOSS: permanent lockout (index-relative, >=25pp excess vs Nifty 500 below 200-DMA).
- Incubation GTT: 40% below avg buy price (INCUBATION_GTT_MULT = 0.60).
- Dynamic GTT_STOP_LOSS: trailing floor (see Section 4.4), index-relative 20pp gate.
- Negative-cash guardrail: amt = min(amt, cash / (1 + FRICTION_RATE)).

### Max open positions / drawdown limits
- No explicit maximum number of open positions in code. The number of holdings is emergent from how many stocks have sip_multiplier > 0 and the monthly budget.
- Max drawdown is measured (not enforced) via _max_drawdown on the daily equity curve; the scorecard reports max_drawdown_pct and return_drawdown_ratio.

---

## 6. Execution & Edge Cases

**Source:** _execute_buy, _execute_sell, _book_proceeds, _intraday_gtt, _monthly_step, run_backtest.

### Order types / slippage / commission
- No limit orders. Buys and sells execute at the current day adjusted-close price.
- GTT_STOP_LOSS sells execute at the monthly review-day market price (cmp), not the historical trigger price; evaluation is monthly-only inside `_monthly_step` (before SIP buys).
- Slippage/commission modelled as a flat FRICTION_RATE = 0.0025 (0.25%) on every trade. Idle cash yields 0.0% (CASH_RATE_PA = 0.0).

### Partial fills and conflicting signals
- _apply_sell takes a fraction: 1.0 for full exits, min(1.0, shares / qty) for concentration trims.
- evaluate_signals returns exactly one signal per stock (first-match priority), so conflicting sell signals cannot both fire in the same month.
- If NO_SIGNAL, the position is held until SIP buys or a later signal.

### Other edge cases
- permanent_exited stocks are never re-bought.
- 90-day cooldown after LAGGARD_EXIT; early re-entry only on a confirmed golden cross.
- Benchmarks deploy the exact same rupees on the exact same dates; index benchmarks accrue the TRI dividend yield.
- Buy deduction is hard-bounded by available cash, so cash never goes negative.
- All numeric values pass through _finite guards (zero/NaN/inf safe).

---

## 7. File & Function Map

| Rule / Component | File | Function / Line |
|---|---|---|
| Signal constants & thresholds | performance/stage4_signals.py | lines ~33-75 |
| analyzeStockSignals | performance/stage4_signals.py | analyzeStockSignals |
| evaluate_signal (Priority 1-4) | performance/stage4_signals.py | evaluate_signal |
| compute_sip_multiplier (lane assignment) | performance/stage4_signals.py | compute_sip_multiplier |
| compute_stock_natural_sip | performance/stage4_signals.py | compute_stock_natural_sip |
| compute_bottom_up_sip_allocation | performance/stage4_signals.py | compute_bottom_up_sip_allocation |
| build_stock_input / StockInput | performance/stage4_signals.py | build_stock_input, StockInput |
| _peak_price_52w | performance/stage4_signals.py | _peak_price_52w |
| rank_bottom_laggards | performance/stage4_signals.py | rank_bottom_laggards |
| Universe assembly & price cache | performance/backtest_data.py | load_backtest_universe, _bulk_download, cached_price_series |
| Benchmark tickers / fallbacks | performance/backtest_data.py | NIFTY50_TICKER, NIFTY500_TICKERS, BENCHMARK_FALLBACKS |
| Backtest entry point | performance/stage4_backtest.py | run_backtest |
| State model | performance/stage4_backtest.py | _StockState |
| Buy execution | performance/stage4_backtest.py | _execute_buy |
| Sell execution (FIFO, tax) | performance/stage4_backtest.py | _execute_sell |
| Proceeds booking | performance/stage4_backtest.py | _book_proceeds |
| Intraday GTT / sell chokepoint | performance/stage4_backtest.py | _intraday_gtt, _apply_sell, _record_sell_stats |
| Monthly step (signals + deploy + sells) | performance/stage4_backtest.py | _monthly_step |
| LAGGARD_EXIT gate | performance/stage4_backtest.py | _laggard_exit_allowed |
| GTT_STOP_LOSS gate | performance/stage4_backtest.py | _gtt_stop_allowed |
| Nifty 500 price w/ fallback | performance/stage4_backtest.py | _nifty500_price |
| Sell breakdown audit | performance/stage4_backtest.py | _build_sell_breakdown |
| Calendar-year returns / attribution | performance/stage4_backtest.py | _calendar_year_returns, _build_attribution |
| Stage 4E UI | app/pages/performance.py | _render_stage4_backtester, _run_stage4_backtest_cached |

---

## Dead Code, Magic Numbers & Incomplete Logic

### Dead / disabled code
- **CATASTROPHE_GTT (45% blind intraday floor):** execution path removed from _intraday_gtt (which is now peak-tracking only; GTT_STOP_LOSS executes monthly-only in _monthly_step). Retained only in SELL_REASON_ORDER so the audit table prints an explicit zero row. It previously set permanent_exited = True / frozen = True.
- _sim_equal_weight_benchmark exists for the equal-weight benchmark curve; it is not part of the strategy rules.

### Hardcoded magic numbers
- Signals (stage4_signals.py): STOP_LOSS_FLOOR=0.20, STOP_LOSS_CAP=0.45, STOP_LOSS_MULT=1.5, HARD_STOP_MIN_INVESTED=150000.0, HARD_STOP_AER_FLOOR=0.50, TRIM_AER=2.20, TRIM_MIN_MARKET_VALUE=200000.0, TRIM_MIN_XIRR=0.25, TRIM_TARGET_MULT=1.40, INCUBATION_MAX_INVESTED=100000.0, INCUBATION_MAX_DAYS=180, INCUBATION_MAX_AGE_DAYS=365, INCUBATION_GTT_MULT=0.60, GTT_BUFFER_WARN=0.10, TRAILING_PEAK_MULT=2.00, TRAILING_STOP_MULT=1.50, TRAILING_STOP_FLOOR=0.35, TRAILING_STOP_CAP=0.45.
- SIP lanes: SIP_RUN_UP_DMA_DISTANCE=0.30, SIP_PULLBACK_DMA_DISTANCE_MAX=0.15, SIP_PULLBACK_AER_MAX=1.30, SIP_LANE1_MULT=2.50, SIP_PULLBACK_WINNER_MARGIN=0.10, SIP_BROKEN_TREND_AER=0.85, SIP_OVERALLOC_CAP_125_PCT=1.25, MAX_SINGLE_STOCK_WEIGHT_PCT=0.08, SIP_GRANULARITY=5000.0.
- Backtest engine (stage4_backtest.py): FRICTION_RATE=0.0025, STCG_RATE=0.20, LTCG_RATE=0.125, LTCG_EXEMPTION=125000.0, CASH_RATE_PA=0.0, COOLDOWN_DAYS=90, LAGGARD_EXIT_MIN_HELD_DAYS=730, LAGGARD_EXIT_MIN_NIFTY_LAG=0.15, GTT_STOP_LOSS_MIN_NIFTY_LAG=0.20, LTCG_HOLD_DAYS=365.

### Incomplete / TODO logic
- No explicit max open-position count is implemented; position count is emergent from budget + eligible SIP stocks.
- No slippage model beyond the flat 0.25% friction; no limit-order support or intraday fills.
- BacktestConfig accepts years/include_watchlist/include_closed_positions/base_unit_sip/etc.; some are only consumed indirectly (ruff F841 flags unused dataclass fields in one lint config). Not dead, but lightly validated.
- _peak_price_52w is defined in stage4_signals.py; verify call sites before removing, as the trailing-stop logic may compute the peak inline elsewhere.
- No enforced drawdown limit / kill-switch; max drawdown is measured and reported, not acted upon.

---

## 9. Exit → Quarantine → Golden-Cross Re-Entry → Two-Strikes Lifecycle (Stage 4C)

**Source:** `performance/stage4_backtest.py` (`_apply_stop_loss_strike`, `_lifecycle_stats`, `_monthly_step`, `run_backtest`) · **UI:** `app/pages/performance.py` (Backtester + Monthly SOP checklist).

### Strike 1 (Quarantine)

Any `GTT_STOP_LOSS` or `HARD_STOP_LOSS` exit moves the ticker to `QUARANTINE` (`strike_count = 1`), sets `sip_multiplier = 0.0`, and enforces a 90-day cooling-off period.

### Golden-Cross Re-Entry

After the 90-day cooldown, a quarantined ticker becomes SIP-eligible again on a monthly review date only when `Close > 200-DMA` AND `50-DMA > 200-DMA`. Importantly, `strike_count` stays armed at `1` across re-entry.

### Strike 2 (Permanent Drop)

If a re-entered ticker triggers a stop-loss a second time, it is permanently marked `DROPPED_TWO_STRIKES` (`permanent_exited = True`, `sip_multiplier = 0.0`) and listed in the Backtester's `dropped_tickers` diagnostics.

## 10. 15-Minute Monthly Review SOP (6-Step Routine)

The interactive checklist is built into the Stage 4C tab of the Performance page as the expander **"📋 15-Minute Monthly Review Checklist (SOP)"** (collapsed by default; UI only, no strategy changes).

1. **Sync Holdings & Prices** (2 min) — Import trades, refresh prices; click Fetch Missing Prices if N/A.
2. **Check Market Regime / Nifty 500 crash-freeze ≥15% DD** (1 min) — Crash-freeze check before acting.
3. **Process Exits & Trims First** (4 min) — Review GTT / LAGGARD / CONCENTRATION trims; sells recycle capital.
4. **Audit Quarantine & Re-Entry Watchlist** (2 min) — QUARANTINE / DROPPED (2 STRIKES): ₹0 SIP; note RE-ENTRY READY.
5. **Deploy Monthly SIPs within ceiling** (4 min) — Buy per Stage 4C recommended SIP within monthly ceiling.
6. **Set / Trail Broker GTTs** (2 min) — Place / trail GTT stop-loss for new buys and updated peaks.

Progress indicator shows `x/6 steps completed`; a Reset button clears the checklist state.

## 11. 7Y Backtest Validation Note (A/B Result)

Verified 7Y backtest impact of enabling Quarantine + Golden-Cross Re-Entry (2-Strikes):

- **Final Net Value:** ₹84,19,931 (+₹1,420 vs baseline)
- **Strategy XIRR:** 18.16% (+0.01%)
- **Max DD:** 26.8%.

