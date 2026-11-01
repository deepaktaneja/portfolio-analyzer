# Holdings Reconciliation Diagnosis

Read-only diagnosis. No database changes.

## Inputs
- XLS holdings: data/portfolio/8500375196_PortFolioEqtSummary.xls
- Transactions: data/xirr.sqlite3
- Corporate actions: data/market_data.sqlite3
- Opening positions: none recorded

## Findings
- XLS contains 19 current securities.
- No opening positions are recorded.
- XLS has no statement as-of date.
- App falls back to transaction end date or user-selected date.
- Reconstructed quantities differ from XLS quantities for 25 ISINs.
- Some reconstructed positions are absent from XLS, likely exited.
- Corporate actions may be provisional.

## Likely causes
1. Missing pre-history opening positions.
2. Reconciliation date mismatch.
3. Corporate actions not verified or incomplete.
4. Rights issues may be auto-counted incorrectly.
5. Exited positions remain in5. Exited positions remain in5. Exited positions remain it a true opening quantity.

## Next steps
- Derive candidate openings from XLS minus net transactions.
- Mark candidates provisional.
- Verify corporate actions.
- Build chronological movement reports.
- Improve reconciliation UI.
