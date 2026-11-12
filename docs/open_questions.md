# Open Questions — Unresolved Decisions and Dependencies

**Stage 0 deliverable.** Items that must be resolved before implementation begins (or before the phases that depend on them). No capabilities or data sources are assumed or invented here.

## 1. Mutual fund MCP server contracts (blocking Phase 4 & 5)

- What are the exact tool names, input schemas, transports, endpoints, and authentication for `mftool-mcp` (primary) and `TigZig Indian MF MCP` (fallback)?
- Are these servers registered anywhere (a registry file, npm package, PyPI package, or endpoint) that the new app can point its own `config/mcp_servers.json` at?
- Do they provide scheme search by ISIN/AMFI code, NAV history, folio-level holdings, SIP, and redemption data?
- **MCP vs API decision:** If no free unlimited MF MCP is available, which free MF NAV API (e.g. AMFI) offers scheme NAV history + scheme details without usage caps?

## 2. Equity price history & corporate actions source (blocking Phase 3 & 5)

- `nse-history` MCP is enabled in the source registry; `nse-market` (current data) is **disabled**. Is `nse-history` reliable for EOD prices and index history?
- If `nse-history` is unavailable, which free API provides NSE EOD price history and corporate actions (splits/bonuses/dividends/mergers)? (yfinance is already in the portfolio-analyzer venv as a reference option.)
- Is end-of-day close sufficient for valuation and XIRR, or is intraday/LTP needed?

## 3. LLM provider and model (blocking Phase 6)

- Fallback chain decided: **Gemini → OpenRouter → local LLM**, falling to the next provider when free quota is exhausted.
- Should the new app use its own `.env` with separate credentials from the research app?
- What temperature/system prompt keeps research deterministic enough for audit? Which local LLM model is acceptable as the final fallback?

## 5. Codebase location (DECIDED: separate directory/venv)

- The new app will live in a brand-new directory with its own virtual environment, to avoid any coupling to `/Users/anmol/portfolio-analyzer` or `/Users/anmol/stock-selection-agent`.

## 6. Indian tax rules for rebalancing (blocking Phase 8)

- STT rates, LTCG/STCG thresholds, equity vs MF tax treatment, holding-period rules, and indexation applicability need to be pinned before tax-aware rebalancing simulation.

## 7. MF NAV source beyond MCP

- If `mftool-mcp` / TigZig is down and no free API is available, is manual CSV import acceptable?

## 8. Analyst-estimate data (requirement: "available analyst-estimate data")

- Which MCP server/tool provides analyst estimates? Likely `company-research` (`analyst_research` / `get_analyst_targets`), but exact fields (target price, rating, EPS estimates) are TBD after server discovery.

## 9. Dependencies on external services

- `nse-history` (https://mcp.nseindia.in/bhavcopy/cm/mcp) is an external HTTPS endpoint. What is the SLA/uptime expectation, and what is the offline fallback (historical CSV)?
- Are there rate limits or API-key requirements for any MCP server or free API the new app will call?

## 10. Deployment and distribution

- Single-user local app (SQLite file on disk) vs multi-user? This affects whether one SQLite file or one-per-user/portfolio, and whether any auth is needed.

## Decision log

| # | Question | Decision | Date | Owner |
|---|---|---|---|---|
| 1 | Codebase location | Separate directory + own venv | 2026-11-10 | Architect |
| 2 | Money type | `Decimal` (SQLAlchemy `Numeric`) | 2026-11-10 | Architect |
| 3 | LLM fallback chain | Gemini → OpenRouter → local LLM (quota-based) | 2026-11-10 | Architect |
| 4 | MF data source | MCP preferred; free API fallback if MCP unavailable | 2026-11-10 | Architect |
| 5 | Equity price/CA source | `nse-history` MCP preferred; free API fallback if disabled/unreliable | 2026-11-10 | Architect |


