# Agent Instructions — Portfolio Analyzer

## Post-experiment protocol
After any strategy backtest experiment, always:
1. Append a row to `EXPERIMENT_LOG.md` with the hypothesis, 3Y/5Y/7Y metrics vs. the previous baseline, and KEEP/REJECT verdict.
2. If KEPT, update `STRATEGY_LOGIC.md` so it stays 100% in sync with the code.

## Strategy context
- Stage 4 Direct Equity engine: `performance/stage4_backtest.py` (engine), `performance/stage4_signals.py` (signals), `app/pages/performance.py` (UI), `performance/backtest_data.py` (data), `src/xirr.py` (XIRR), `app/shared.py` (DB loaders).

## Verification commands (after any edit)
- `python -m py_compile <files>`
- `.venv/bin/ruff check <files>`
- `.venv/bin/python -m pytest tests/test_comprehensive_audit.py -v`
- Regression: `.venv/bin/python -m pytest tests/test_locked_rules.py tests/test_golden_snapshot.py tests/test_master_audit.py tests/unit/test_stage4_signals.py tests/unit -q`

## Current kept strategy state (as of latest experiments)
- LAGGARD_EXIT refinement: held ≥730d & ≥15pp lag → **KEEP**.
- CATASTROPHE_GTT: disabled (blind 45% floor + permanent ban removed) → **KEEP**.
- GTT_STOP_LOSS: index-relative, stock_dd − nifty_dd ≥ 20pp → **KEEP**.
- AER: Allocation Expansion Ratio (market_weight / cost_weight, default 1.0) → formalized, behavior unchanged → **KEEP**.
- Recycled sell proceeds: budget capped only by friction (not monthly ceiling) → **KEEP**.
- Monthly-only GTT (Exp #6): **KEEP** — Variant B (monthly-only) permanently wired; +0.47pp XIRR / +1.32pp Max DD vs daily, GTT stops 42 vs 64, Final Net Value +₹139,052 (7Y).
- Incubation below 200-DMA (Exp #7): **KEEP** — incubation SIP 0.5x → 0.0x while below 200-DMA; 7Y XIRR 18.83% / Max DD 26.86% / Final Net Value ₹86.26L (5Y: 17.94%, 3Y: 0.67%).
- Nifty 500 −20% Market Crash Freeze (Exp #8): **KEEP** — crash-freeze wired (`CRASH_FREEZE_NIFTY_DD_THRESHOLD = -0.20`) pausing GTT_STOP_LOSS / LAGGARD_EXIT sells during monthly reviews with Nifty 500 trailing-52w drawdown < −20%; Post-Exp #8 baseline 7Y XIRR 19.24% / Max DD 26.86% / Final Net Value ₹87.52L (5Y: 17.94%, 3Y: 0.67%).
- Incubation below 200-DMA (Exp #7): **KEEP** — incubation SIP 0.5x → 0.0x while below 200-DMA; 7Y XIRR 18.83% / Max DD 26.86% / Final Net Value ₹86.26L (5Y: 17.94%, 3Y: 0.67%).
