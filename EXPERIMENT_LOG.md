# Strategy Experiment Log

Single-rule experiments run against the Stage 4 Direct Equity backtest engine (`performance/stage4_backtest.py`).

## Protocol (agent rule — going forward)

After any strategy backtest experiment, always:

1. Append a row to `EXPERIMENT_LOG.md` with the hypothesis, 3Y/5Y/7Y metrics vs. the previous baseline, and KEEP/REJECT verdict.
2. If KEPT, update `STRATEGY_LOGIC.md` so it stays 100% in sync with the code.

## Config note

From Experiment #5 onward (and reused in Experiment #6), all backtest runs used the canonical full-scorecard configuration:

- `BacktestConfig(years=7, include_watchlist=True, include_closed_positions=True, market_db_path="data/market_data.sqlite3")`
- 7-year window: 2019-11-20 → 2026-11-20
- Full-scorecard (Post-Exp #6, kept monthly-only GTT) baseline: Post-tax XIRR 17.91% | Max DD 34.41% | Final Idle Cash ₹2.70L | Final Net Value ₹83.45L

Earlier experiments (#1–#5) used an earlier config; from #5 onward the canonical config was used.

## Baseline #0 — Original engine (pre-experiments)

| Metric | 7Y Value |
|---|---|
| Post-tax XIRR | 10.62% |
| Max Drawdown | 32.15% |
| Final Idle Cash | ₹42.97L |
| Final Net Value | ₹64.07L |

## Experiment #1 — LAGGARD_EXIT refinement

- **Hypothesis:** LAGGARD_EXIT triggers only when held ≥730 calendar days AND lagged Nifty 500 by ≥15pp over the holding period.
- **7Y Post-tax XIRR:** 11.58% | **7Y Max DD:** 32.15% | **7Y Idle Cash:** ₹42.25L | **7Y Final Net Value:** ₹66.34L
- **Verdict:** **KEEP**

## Experiment #2 — CATASTROPHE_GTT disabled

- **Hypothesis:** Remove blind 45%-below-peak daily liquidation + permanent ban; rely on index-relative HARD_STOP_LOSS + GTT_STOP_LOSS.
- **7Y Post-tax XIRR:** 13.70% | **7Y Max DD:** 32.74% | **7Y Idle Cash:** ₹17.68L | **7Y Final Net Value:** ₹71.69L
- **Verdict:** **KEEP**

## Experiment #3 — GTT_STOP_LOSS index-relative (20pp excess drawdown)

- **Hypothesis:** GTT_STOP_LOSS fires only when `stock_dd - nifty_dd >= 0.20`.
- **7Y Post-tax XIRR:** 14.24% | **7Y Max DD:** 33.09% | **7Y Idle Cash:** ₹16.27L | **7Y Final Net Value:** ₹73.09L
- **Verdict:** **KEEP**

## Experiment #4 — AER formalization + diagnostics

- **Hypothesis:** Formalize `aer` as Allocation Expansion Ratio (`market_weight / cost_weight`, default 1.0, zero-cost guard); add min/median/max `aer` + idle-cash diagnostics. Behavior unchanged.
- **7Y Post-tax XIRR:** 14.24% | **7Y Max DD:** 33.09% | **7Y Idle Cash:** ₹16.27L | **7Y Final Net Value:** ₹73.09L
- **Verdict:** **KEEP** (formalization only; behavior unchanged)

## Experiment #5 — Uncap recycled sell proceeds (remove monthly budget ceiling on redeployment)

- **Hypothesis:** Keep `max_monthly_ceiling` only for fresh inflow; cap deployable buy budget only by friction (0.25%).
- **7Y Post-tax XIRR:** **15.46%** | **7Y Max DD:** **33.09%** | **7Y Idle Cash:** **₹2.82L** | **7Y Final Net Value:** **₹76.40L**
- **Verdict:** **KEEP**

## Experiment #6 — Monthly-Only GTT_STOP_LOSS (A/B test, read-only)

- **Hypothesis:** Evaluate GTT_STOP_LOSS only on monthly review days (sell at review-day price `px`, GTT check before buys, same 20pp Nifty-relative rule) for better compounding vs daily check.

- **A/B baseline (full scorecard, Post-Exp #5 config):** Post-tax XIRR 17.44% | Max DD 33.09% | Final Idle Cash ₹2.76L | Final Net Value ₹82.06L | GTT stops 64.

- **Variant B (monthly-only):** Post-tax XIRR 17.91% | Max DD 34.41% | Final Net Value **₹83.45L (₹8,345,330)** | Final Idle Cash ₹2.70L | GTT stops 42.

- **Delta (B − A):** Post-tax XIRR +0.47pp | Final Net Value +₹139,052 | Max DD +1.32pp | GTT stops −22.

- **Verdict:** **KEEP** — permanently wired into `performance/stage4_backtest.py` (GTT stop evaluated in `_monthly_step` at review-day price `cmp` before SIP buys; `_intraday_gtt` reduced to peak tracking only).

- **Post-wiring 3Y / 5Y / 7Y scorecard (monthly-only GTT, canonical config):**

| Window | Post-tax XIRR | Max DD | Final Idle Cash | Final Net Value |
|---|---|---|---|---|
| 3Y | **4.35%** | 13.69% | ₹0.00 | ₹1,098,247.51 |
| 5Y | **16.34%** | 20.93% | ₹0.00 | ₹5,919,741.65 |
| 7Y | **17.91%** | 34.41% | ₹270,182.63 | ₹8,345,329.62 |

## 3Y / 5Y / 7Y quick reference (Post-Exp #6, canonical config, monthly-only GTT)

| Window | Post-tax XIRR | Max DD | Final Idle Cash | Final Net Value |
|---|---|---|---|---|
| 3Y | **4.35%** | 13.69% | ₹0.00 | ₹1,098,247.51 |
| 5Y | **16.34%** | 20.93% | ₹0.00 | ₹5,919,741.65 |
| 7Y | **17.91%** | 34.41% | ₹270,182.63 | ₹8,345,329.62 |

## Experiment #7 - Incubation below 200-DMA: 0.5x to 0.0x (KEEP)

- **Hypothesis:** Pause starter SIPs (0.5x to 0.0x) for incubating holdings trading below 200-DMA; resume once CMP >= 200-DMA. Non-incubating holdings already get 0.0x below 200-DMA (pre-existing).

- **Code change:** performance/stage4_signals.py - incubation override in evaluate_signals: dma200_distance_pct < 0 now yields sip = 0.0 (was 0.5); > +30 pct run-up stays at 0.5.

- **Side-by-side vs Post-Exp #6 Baseline:**

| Window | Post-tax XIRR | Alpha vs N500 | Final Net Value | Final Idle Cash | Max DD | Trade Count |
|--------|--------------|---------------|-----------------|-----------------|--------|-------------|
| 3Y | 0.67% (base 4.35%) | -7.83pp (base -4.15pp) | 1,053,181 (base 1,098,247) | 5,171 (base 0) | 13.19% (base 13.69%) | 227 (base 290) |
| 5Y | 17.94% (base 16.34%) | +5.18pp (base +3.58pp) | 6,208,524 (base 5,919,742) | 0 (base 0) | 21.40% (base 20.93%) | 903 (base 1,145) |
| 7Y | 18.83% (base 17.91%) | +4.91pp (base +3.99pp) | 8,626,064 (base 8,345,330) | 304,842 (base 270,183) | 26.86% (base 34.41%) | 1,221 (base N/A) |

- **Delta (Exp #7 - Baseline):**
  - 3Y: XIRR -3.68pp (worse), Alpha -3.68pp, Net Value -45,066, Max DD -0.50pp (better), Trades -63
  - 5Y: XIRR +1.60pp (better), Alpha +1.60pp, Net Value +288,782, Max DD +0.47pp (worse), Trades -242
  - 7Y: XIRR +0.92pp (better), Alpha +0.92pp, Net Value +280,734, Idle Cash +34,659, Max DD -7.55pp (significantly better)

- **Bottom 5 Detractors (7Y):** ABFRL (-84,724, 25 trades), FAIRCHEMOR (-68,754, 19 trades), VEDL (-41,878), ROUTE (-36,823), HDIL (-34,617, 14 trades). 3 of 5 are target stocks (ABFRL, FAIRCHEMOR, HDIL).

- **Verdict:** **KEEP** - Mixed results. 5Y and 7Y show improved XIRR and significantly lower Max DD (7Y: 26.86% vs 34.41%), but 3Y degrades materially (XIRR 0.67% vs 4.35%). Idle cash increases slightly. Awaiting review before KEEP/REJECT.

## Experiment #8 - Nifty 500 -20% Market Crash Freeze (KEEP)

- **Hypothesis:** During a broad market crash (Nifty 500 trailing-52w-high drawdown < -20%), pause the defensive `GTT_STOP_LOSS` and `LAGGARD_EXIT` sells for that monthly review month, so index-driven risk reduction does not add to drawdown during a crash.

- **Code change (diagnostic wiring):** performance/stage4_backtest.py - added `CRASH_FREEZE_NIFTY_DD_THRESHOLD = -0.20`; new `_nifty500_52w_drawdown(prices, d)` (trailing 52w-high drawdown of `^CRSLDX` / `MONIFTY500.NS`, negative fraction; `None` when benchmark unavailable); `_monthly_step` computes `crash_freeze` per review month and gates `LAGGARD_EXIT` + `GTT_STOP_LOSS` on it. `HARD_STOP_LOSS` / `CONCENTRATION_TRIM` / SIP buys unchanged; no freeze when benchmark data unavailable. Strategy rules NOT yet permanently wired (diagnostic only).

- **Side-by-side vs Post-Exp #7 baseline** (canonical config: `BacktestConfig(years=N, include_watchlist=True, include_closed_positions=True, market_db_path="data/market_data.sqlite3")`):

| Window | Post-tax XIRR | Max DD | Final Idle Cash | Final Net Value | Trade Count | Freeze Engaged |
|---|---:|---:|---:|---:|---:|---:|
| 3Y | 0.67% | 13.19% | Rs 5,171 | Rs 1,053,181 | 227 | No |
| 5Y | 17.94% | 21.40% | Rs 0 | Rs 6,208,524 | 903 | No |
| 7Y | 19.24% | 26.86% | Rs 2,40,065 | Rs 8,751,774 | 1,230 | **Yes** |

- **Delta (Exp #8 - Post-Exp #7):** 3Y / 5Y runs are identical to baseline (freeze never engaged in those windows). 7Y: post-tax XIRR +0.41pp (18.83% -> 19.24%), Final Net Value +Rs 1,25,710, Final Idle Cash -Rs 64,777, Trades +9; Max DD unchanged (26.86%).

- **Crash Freeze diagnostic (7Y):** freeze engaged on monthly review dates `2020-04-01`, `2020-05-01`, `2020-06-01` (COVID crash); Nifty 500 trailing-52w drawdowns -0.3317, -0.2081, -0.2074; suppressed GTT_STOP_LOSS sells: 16; suppressed LAGGARD_EXIT sells: 0.

- **Verdict:** **KEEP** - Diagnostic wiring validated on the real portfolio: freeze engaged only in the 7Y backtest during COVID review months (2020-04-01/05-01/06-01; Nifty DD -0.3317/-0.2081/-0.2074); suppressed 16 GTT_STOP_LOSS sells (0 LAGGARD_EXIT). 3Y/5Y freeze not engaged; 7Y XIRR +0.41pp (18.83% -> 19.24%), Final Net Value +₹1,25,710, Idle Cash -₹64,777, Max DD unchanged (26.86%). Rule kept as documented crash-freeze behaviour.
