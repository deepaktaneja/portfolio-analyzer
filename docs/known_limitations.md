# Known Limitations

- OCR not implemented; scanned PDFs are flagged only.
- Money uses float, not Decimal.
- Corporate actions require manual verification for mergers/demergers.
- Dividends counted only when verified credits exist.
- Market data last-available price used for non-trading days (no interpolation).
- No production-readiness claim; real ICICI files tested for import only.
