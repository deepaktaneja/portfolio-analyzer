# Debug Diagnosis Report

## 1. Quantity discrepancy
Root cause: Portfolio uses XLS snapshot; Stock Analysis uses reconstructed transactions.
ledger.positions.reconstruct ignores corporate actions and openings.
Affected: app.py, ledger/positions.py, ledger/opening.py, corporate_actions/engine.py

## 2. Blank/slow tabs
Annual Performance hangs: O(n^2) over 3075 transactions; full run >60s at 100% CPU.
Benchmark Analysis: no TRI data; metrics None.
Gains and Dividends: FIFO not wired to corporate actions; missing prices -> NULL.
Data Management: reconcile/duplicate checks slow; no empty states.
Market Data: wired; sync can be long.

## 3. Recommended fix order
1 Performance cache and index lookups.
2 Single holdings service.
3 Corporate-action wiring into gains.
4 TRI import and benchmark wiring.
5 Empty states and error handling.
