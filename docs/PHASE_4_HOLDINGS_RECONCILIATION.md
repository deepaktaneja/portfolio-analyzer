# Phase 4 Holdings Reconciliation

## Root causes
- No opening positions recorded.
- XLS has no as-of date; app fell back to transaction end date.
- Corporate actions may be provisional.
- Exited positions remained in reconstructed totals.
- Minimum-opening inference was not a true opening quantity.

## Files changed
- `ledger/holdings_service.py`
  - Added reverse opening candidate derivation.
  - Improved chronological movement report with corporate-action deltas and deterministic ordering.
- `app.py`
  - Added candidate opening positions UI.
  - Reconciliation rows include classification and differences.
- `tests/unit/test_holdings_service.py`
  - Added reverse opening and movement tests.

## Calculations corrected
- Reverse-derived candidate openings: `opening = XLS quantity - net transactions`.
- Movement reports now include splits, bonuses, rights, mergers, demergers, buybacks with running balances.
- No artificial BUY transactions created.

## Tests
- `tests/unit/test_holdings_service.py`: 13 passed.

## Reconciled securities
- 19 XLS securities remain under review.
- Candidate openings are provisional until manually verified.

## Remaining discrepancies
- Corporate actions must be verified manually.
- XLS statement date must be confirmed.
- Some positions may require missing transaction records.

## Missing source records
- Pre-history purchases.
- Verified corporate-action confirmations.
- XLS statement as-of date.