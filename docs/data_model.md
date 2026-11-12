# Data Model — Database Entities and Relationships

**Stage 0 deliverable.** Logical schema for the standalone portfolio intelligence application. Implemented in Stage 1 via SQLAlchemy 2.0 declarative models + Alembic migrations. Single SQLite database file; `portfolio_id` scopes multi-portfolio support. No changes to any other application's database.

## 1. Naming and conventions

- Table names: `snake_case`, plural where natural (`portfolios`, `transactions`).
- Primary keys: `id INTEGER PRIMARY KEY AUTOINCREMENT` (surrogate) or natural key where domain-stable (`securities.isin`, `mf_schemes.scheme_code`).
- All `date`/`datetime` columns stored as ISO-8601 TEXT (UTC); displayed in IST in the UI.
- Monetary quantities stored as `DECIMAL` (SQLAlchemy `Numeric`) for exact cents-level precision; rounded only at presentation. Money type decided: `Decimal`.
- Every derived artefact (holding, XIRR run, signal, report) carries foreign keys back to source rows for full audit.

## 2. Entity relationship diagram (textual)

```
portfolios 1:N holdings, transactions, mf_folios, review_signals, research_reports, portfolio_valuations, backtest_runs
securities 1:N transactions, corporate_actions, dividends, market_prices, holdings
mf_schemes 1:N mf_folios, mf_nav, mf_transactions
mf_folios 1:N mf_transactions
backtest_runs 1:N xirr_cashflows
```

## 3. Core tables

### portfolios
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT NOT NULL | user-facing portfolio name |
| base_currency | TEXT NOT NULL DEFAULT 'INR' | |
| created_at | TEXT NOT NULL | ISO-8601 UTC |

### securities
| Column | Type | Notes |
|---|---|---|
| isin | TEXT PK | 12-char ISIN for equities; scheme-code-style key for MF |
| symbol | TEXT | trading symbol |
| name | TEXT NOT NULL | |
| exchange | TEXT | NSE/BSE |
| sector | TEXT | |
| instrument_type | TEXT NOT NULL | 'EQUITY' or 'MF' |
| is_active | INTEGER NOT NULL DEFAULT 1 | |

### holdings (derived, recomputed — not source of truth)
| Column | Type | Notes |
|---|---|---|
| portfolio_id | INTEGER FK → portfolios.id | |
| security_id | TEXT FK → securities.isin | |
| quantity | REAL NOT NULL | remaining FIFO quantity |
| cost_basis | REAL NOT NULL | remaining FIFO cost |
| updated_at | TEXT NOT NULL | |
PK: (portfolio_id, security_id)

### transactions (normalised statement rows)
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| portfolio_id | INTEGER FK | |
| security_id | TEXT FK → securities.isin | |
| trade_date | TEXT | ISO date |
| settlement_date | TEXT | ISO date |
| side | TEXT | BUY/SELL/SIP/REDEMPTION/SWITCH_IN/SWITCH_OUT/DIVIDEND/OPENING |
| quantity | REAL | |
| price | REAL | per-unit price |
| gross_amount | REAL | |
| brokerage | REAL DEFAULT 0 | |
| gst | REAL DEFAULT 0 | |
| charges | REAL DEFAULT 0 | |
| net_amount | REAL | gross_amount ± brokerage/gst/charges |
| source_file | TEXT | originating statement |
| fingerprint | TEXT | SHA-256 dedup key |
| imported_at | TEXT | |
Index: (portfolio_id, security_id), (fingerprint), (source_file)

### corporate_actions
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| security_id | TEXT FK → securities.isin | |
| event_type | TEXT | split/bonus/rights/merger/demerger/buyback |
| ex_date | TEXT | |
| record_date | TEXT | |
| effective_date | TEXT | used for lot application ordering |
| ratio | REAL | split/bonus/merger ratio |
| successor_id | TEXT | for merger/demerger |
| cost_allocation | REAL | cost split for merger/demerger |
| verified | INTEGER NOT NULL DEFAULT 0 | unverified mergers/demergers are skipped and flagged |
| verification_status | TEXT | 'manual'/'provisional'/'verified' |
| source | TEXT | 'manual' or MCP server name |
Index: (security_id, event_type, effective_date)

### dividends
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| security_id | TEXT FK → securities.isin | |
| ex_date | TEXT | |
| record_date | TEXT | |
| payment_date | TEXT | cash-flow date used in XIRR |
| gross_per_share | REAL | |
| currency | TEXT DEFAULT 'INR' | |
| source | TEXT | |
| status | TEXT DEFAULT 'confirmed' | |

### market_prices
| Column | Type | Notes |
|---|---|---|
| security_id | TEXT FK → securities.isin | |
| trade_date | TEXT | |
| close_price | REAL | |
| source | TEXT | MCP server name / yfinance / manual_csv |
| provider | TEXT | |
| fetched_at | TEXT | |
PK: (security_id, trade_date)

### index_levels
| Column | Type | Notes |
|---|---|---|
| index_id | TEXT PK part 1 | e.g. 'NIFTY50TRI', 'NIFTY500TRI' |
| trade_date | TEXT PK part 2 | |
| level | REAL | TRI level |
| source | TEXT | |


## 4. Mutual fund tables

### mf_schemes
| Column | Type | Notes |
|---|---|---|
| scheme_code | TEXT PK | AMFI scheme code |
| scheme_name | TEXT NOT NULL | |
| fund_house | TEXT | |
| category | TEXT | e.g. 'Flexi Cap', 'Liquid' |
| amfi_code | TEXT | |
| plan | TEXT | 'Regular'/'Direct' |
| option | TEXT | 'Growth'/'IDCW' |

### mf_folios
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| portfolio_id | INTEGER FK → portfolios.id | |
| scheme_code | TEXT FK → mf_schemes.scheme_code | |
| folio_number | TEXT | |
| rta | TEXT | 'CAMS'/'Karvy'/'other' |

### mf_nav
| Column | Type | Notes |
|---|---|---|
| scheme_code | TEXT FK → mf_schemes.scheme_code | |
| nav_date | TEXT | |
| nav | REAL | |
| source | TEXT | mftool-mcp / TigZig / AMFI fallback |
PK: (scheme_code, nav_date)

### mf_transactions
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| folio_id | INTEGER FK → mf_folios.id | |
| transaction_type | TEXT | PURCHASE/REDEMPTION/SWITCH_IN/SWITCH_OUT/DIVIDEND |
| date | TEXT | |
| units | REAL | |
| nav | REAL | |
| amount | REAL | |
| source | TEXT | |

## 5. Performance, backtest, and decision tables

### portfolio_valuations (historical snapshots)
| Column | Type | Notes |
|---|---|---|
| date | TEXT PK part 1 | |
| portfolio_id | INTEGER PK part 2 | |
| security_id / scheme_code | TEXT | security or scheme |
| quantity | REAL | |
| price | REAL | |
| value | REAL | |

### xirr_cashflows (auditable inputs to every XIRR run)
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| run_id | TEXT | groups cash flows for one XIRR computation |
| scope | TEXT | 'stock'/'fund'/'portfolio' |
| security_id / scheme_code | TEXT | |
| date | TEXT | cash-flow date |
| amount | REAL | negative = outflow, positive = inflow |
| label | TEXT | 'buy'/'sell'/'dividend'/'terminal_value' |
| created_at | TEXT | |

### backtest_runs
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT | user label |
| original_security | TEXT FK → securities.isin | |
| alternative_security | TEXT FK → securities.isin | |
| start_date | TEXT | |
| end_date | TEXT | |
| actual_xirr | REAL | |
| hypothetical_xirr | REAL | |
| cost_basis | REAL | assumed transaction cost |
| notes | TEXT | |

### review_signals (decision intelligence output — advisory only)
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| portfolio_id | INTEGER FK | |
| security_id | TEXT FK → securities.isin | nullable (portfolio-level signals) |
| signal_type | TEXT | HOLD/MONITOR/TRIM/EXIT |
| reasons | TEXT | JSON array of explainable reasons |
| severity | TEXT | low/medium/high |
| generated_at | TEXT | |
| basis | TEXT | 'accounting'/'mcp_research'/'llm' |

### research_reports (LLM-generated, evidence-cited)
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| portfolio_id | INTEGER FK | |
| security_id | TEXT FK → securities.isin | nullable |
| title | TEXT | |
| content_md | TEXT | |
| evidence_ids | TEXT | JSON array of mcp_cache evidence_id values |
| model | TEXT | LLM model used |
| generated_at | TEXT | |

## 6. Operational tables

### mcp_cache
| Column | Type | Notes |
|---|---|---|
| server_name | TEXT PK part 1 | |
| tool_name | TEXT PK part 2 | |
| args_hash | TEXT PK part 3 | SHA-256 of canonicalised arguments |
| arguments | TEXT | JSON |
| response | TEXT | JSON |
| retrieved_at | TEXT | |
| expires_at | TEXT | TTL |
| hit_count | INTEGER DEFAULT 0 | |

### import_audit
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| source_file | TEXT | |
| sha256 | TEXT | |
| detected_type | TEXT | 'equity_pdf'/'equity_xls'/'mf_consolidated' |
| row_count | INTEGER | |
| status | TEXT | 'imported'/'rejected'/'partial' |
| imported_at | TEXT | |

## 7. Integrity rules

- `fingerprint` on `transactions` prevents duplicate re-import of the same statement row.
- `corporate_actions.verified = 0` for merger/demerger → those actions are skipped in lot reconstruction and flagged for manual review (no silent assumption).
- XIRR `run_id` + `xirr_cashflows` make every XIRR result fully reproducible: given the same run_id, the same cash flows produce the same XIRR.
- `mcp_cache.args_hash` makes MCP re-fetches idempotent within TTL.

