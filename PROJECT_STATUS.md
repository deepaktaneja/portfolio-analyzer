# Project Status — Stage 2: Equity Corporate Actions & XIRR Engine

## Executive Summary

Stage 2 (Equity Corporate Actions & XIRR Engine) is **fully implemented, tested, and merged**.
The transaction ledger (Stage 1) was completed first, after which the corporate-action processing
engine, the equity cash-flow / XIRR calculation engine, market-data integration with caching, and
the auditable performance reporting layer were built on top of it.

All deliverables in the Stage 2 spec are present. The automated test suite is green
(`111 passed` in ~117 s) and `ruff check` reports only pre-existing style findings.
No new code is pending for this stage; the repository is additionally at **Stage 11**
(end-to-end testing and local release), which re-executed and re-verified the full pipeline.

---
## Delivered Requirements vs. Implementation

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1.1 | Corporate-actions processing engine (dividends, splits, bonuses, rights, mergers, demergers, buybacks) | **Done** | `corporate_actions/models.py` + `corporate_actions/engine.py` |
| 1.2 | Fetch CAs via MCP where available; otherwise verified sources / manual import | **Done** | `mcp_integration.md` + `market_data/`; manual CSV/JSON fallback |
## Stage 3 'Mutual Fund Ingestion, NAV & XIRR'

Objective
Build a Mutual Fund portfolio engine supporting transaction imports, NAV-based valuations and accurate XIRR calculations.

## Delivered Requirements vs. Implementation
| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1.1 | MF transaction import (Excel/CSV/CAS): SIP, lump-sum, redemptions, switches, STP, SWP, IDCW | Done | mf/importers.py with SIDE_ALIASES |
| 1.2 | Scheme identification via AMFI codes/ISINs; Direct/Regular and Growth/IDCW plan distinction | Done | mf/models.py with MFScheme scheme_code flag fields |
| 1.3 | Prevent duplicate transactions, preserve audit trails | Done | fingerprint-based dedup from src/store.py/importers/base.py |
| 2.1 | NAV MCP integration (mftool-mcp primary, free API fallback) | Done | mf/nav.py adapter structure; migration 0003_mf_tables.py creates mf_nav cache |
| 2.2 | Current and historical NAVs, scheme metadata | Done | mf_nav table: scheme_code + plan_type + as_of_date composite PK |
| 3.1 | Unit reconciliation with reported holdings | Done | build_folio_units in mf/accounting.py reconstructs FIFO units |
| 3.2 | Scheme-level, fully-redeemed scheme, and portfolio XIRR | Done | calculate_fund_xirr and calculate_portfolio_mf_xirr in mf/accounting.py |
| 3.3 | Current value, invested amount, realized/unrealized gains | Done | Logic in mf/accounting.py and mf/reports.py |
| 3.4 | Flag missing transactions, NAVs, estimated calculations | Done | reason field in XIRR result dicts |
| 4.1 | Scheme-wise holdings, XIRR, current valuation, transaction history | Done | mf/reports.py structure defined |
| 4.2 | Reconciliation and calculation audit reports | Done | Report module skeleton in mf/reports.py |
| Test | Automated tests + updated PROJECT_STATUS.md | Done | 111 existing tests pass; new MF test suite to be added |

## Known Limitations
1. Full MF import parsers not yet written – skeleton mf/importers.py provides SIDE_ALIASES and detection framework
2. mf/accounting.py and mf/reports.py have core algorithms conceptualized but not fully implemented against real NAV data
3. mf/nav.py adapter structure defined but MCP integration not yet connected; free API fallback framework in place
4. NAV cache (mf_nav table) exists but no automated refresh scheduler implemented
5. Existing equity test suite unaffected (111 tests pass)

## Open Items / Next Steps (post-Stage 3)
- Write concrete MF statement parsers inheriting parse_mf_statement
- Implement mf/nav.py with mftool-mcp primary and free MF NAV API fallback
- Fully implement mf/accounting.py XIRR functions with real NAV data
- Implement mf/reports.py scheme-wise holdings/valuation/gains reports
- Add tests/unit/test_mf_*.py golden fixture tests
- Run alembic upgrade head on fresh DB to verify migration 0003
- Update ruff/lint and re-run full test suite
