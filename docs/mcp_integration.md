# MCP Integration — Capabilities, Adapters, and Gaps

**Stage 0 deliverable.** Specification for integrating Model Context Protocol (MCP) servers into the standalone portfolio intelligence application. The new app reuses existing MCP servers **directly as external services** via the official `mcp` Python SDK. It does **not** import any code from the existing Indian Equity Research Application (`/Users/anmol/stock-selection-agent`) and maintains its own registry configuration.

## 1. Principle

- MCP servers are external processes/endpoints; the app is a client only.
- Every MCP call is validated against the server's **discovered** tool list before execution; calling an undiscovered tool raises `ValueError` (prevents tool hallucination).
- Call arguments are validated against the discovered `inputSchema`.
- Every result is wrapped in a deterministic **evidence envelope** and persisted to `mcp_cache`; `research_reports.evidence_ids` cites envelope IDs so LLM research is traceable to source data.
- Graceful degradation: if a server is unreachable, the app falls back to stale cache or returns a `partial` envelope with a warning — it never crashes the UI.

## 2. Servers to integrate

Source of truth for the existing research-app servers: `/Users/anmol/stock-selection-agent/config/mcp_servers.json` (read-only reference only; the new app keeps its own copy).

### Equities / fundamental research (reuse from research app)

| Server | Transport | Endpoint / Launcher | Enabled (in source registry) | Capabilities |
|---|---|---|---|---|
| `company-research` | stdio | `uvx --python <sys.executable> --from screener-mcp[ai] screener-mcp` | Yes | financial_statements, financial_ratios, quarterly_earnings, peer_comparison, shareholding, promoter_pledging, corporate_disclosures, annual_reports, management_guidance, analyst_research |
| `bulk-screener` | stdio | `bulk-screener-adapter` | Yes | bulk_screening |
| `nse-history` | streamable_http | `https://mcp.nseindia.in/bhavcopy/cm/mcp` | Yes | historical_prices, index_history |
| `nse-market` | streamable_http | `https://mcp.nseindia.in/cmmkt/mcp` | **No (disabled)** | current_market_data → treated as a gap |

### Mutual funds (prefer MCP; fall back to a free unlimited API if MCP is unavailable)

| Server / Source | Transport | Endpoint / Launcher | Status | Capabilities |
|---|---|---|---|---|
| `mftool-mcp` | TBD | TBD | **Primary candidate** | mf_nav, mf_folio, mf_sip, mf_scheme_details (exact tool names TBD via discovery) |
| `TigZig Indian MF MCP` | TBD | TBD | **Fallback candidate** | mf_nav, mf_folio, mf_sip (exact tool names TBD via discovery) |
| Free MF NAV API (e.g. AMFI) | HTTP/REST | TBD | **API fallback** | scheme NAV history, scheme details (if free & unlimited) |

> **Important:** `mftool-mcp` and `TigZig Indian MF MCP` are **not** configured anywhere in either codebase. Their exact tool names, schemas, transports, endpoints, and authentication are unknown and must be discovered (see `open_questions.md`). No capabilities are assumed or invented here. The MF data layer is designed as an abstract source adapter so it can switch between MCP and a free API without changing accounting code.

### Equity price history & corporate actions (prefer `nse-history` MCP; fall back to free API if disabled/unreliable)

| Server / Source | Transport | Endpoint / Launcher | Status | Capabilities |
|---|---|---|---|---|
| `nse-history` | streamable_http | `https://mcp.nseindia.in/bhavcopy/cm/mcp` | **Primary (enabled in source registry)** | historical_prices, index_history |
| Free equity price API (e.g. yfinance / other free NSE API) | HTTP/REST | TBD | **API fallback** | EOD price history |
| Free corporate-action API (e.g. NSE/BSE disclosures / aggregator) | HTTP/REST | TBD | **API fallback** | splits, bonuses, dividends, mergers |

If `nse-history` is disabled or unreliable at runtime, the market-data provider abstraction falls back to a free API for equity price history and corporate actions.

## 3. Discovered tool → capability mapping (equities)

Mirroring the research app's `TOOL_CAPABILITIES` guardrail, the new app's adapter maintains its own mapping:

| Tool | Capabilities |
|---|---|
| `screen_stocks` | bulk_screening |
| `get_financials` | financial_statements, financial_ratios |
| `get_quarterly_results` | quarterly_earnings |
| `get_peer_comparison` / `compare_companies` | peer_comparison |
| `get_shareholding_pattern` | shareholding, promoter_pledging |
| `analyze_red_flags` / `get_company_announcements` / `get_insider_trading` | corporate_disclosures |
| `get_document_list` / `ask_company_research` | annual_reports |
| `get_forward_outlook` | management_guidance, analyst_research |
| `get_analyst_targets` / `get_recent_news` | analyst_research |
| `get_chart` / `get_stock_history` | historical_prices |
| `get_index_history` | index_history |
| `get_stock_vs_index` | historical_prices, index_history |
| `get_corporate_actions` | corporate_disclosures |

MF tool names will be added after `mcp` client discovery against `mftool-mcp` / TigZig.

## 4. Adapter design (`mcp/adapter.py`)

- One `MCPClientManager` loads the app's own `config/mcp_servers.json`.
- Trusted-target validation: stdio launchers and streamable_http URLs must match pre-approved entries (mirrors the research app's `MCPServerDefinition` validator).
- `connect(server_name)` opens an `mcp` SDK `ClientSession` (stdio or streamable_http), calls `list_tools`, and builds a `MCPServerConnection` holding discovered tools + an asyncio semaphore for `max_concurrency`.
- `call_tool(server, tool, arguments)`:
  1. Verifies `tool` is in the discovered set; otherwise raises `ValueError`.
  2. Validates `arguments` against `inputSchema` (jsonschema).
  3. Executes the call; builds an evidence envelope.
- Evidence envelope (persisted to `mcp_cache`):

```json
{
  "evidence_id": "<uuid4 hex>",
  "server": "<server_name>",
  "tool": "<tool_name>",
  "arguments": {...},
  "retrieved_at": "<ISO-8601 UTC>",
  "status": "ok|error|partial",
  "partial": true|false,
  "warnings": [...],
  "data": {...},
  "meta": {}
}
```

- `research_reports.evidence_ids` stores the list of `evidence_id` values cited by an LLM report → full traceability.

## 5. Caching strategy

Table: `mcp_cache(server_name, tool_name, args_hash, arguments, response, retrieved_at, expires_at, hit_count)`.

- `args_hash` = SHA-256 of canonicalised (sorted-key JSON) arguments → idempotent re-fetch dedup.
- TTL by capability class (configurable):
  - Historical prices / NAV / index levels: long (7–30 days).
  - Quarterly earnings / financial statements: medium (1–7 days).
  - Current prices / short-term data: short (minutes–hours) or no cache.
- On cache hit and not expired → return cached response, increment `hit_count`.
- On miss or error → call server, store response, set `expires_at`.
- Circuit-breaker: consecutive failures on a server → short-circuit to stale cache or `partial` envelope with warning; UI shows a clear degradation notice.

## 6. Known gaps (do not assume)

1. `nse-market` MCP is **disabled** in the source registry → no confirmed real-time/current-price MCP for equities. Fallback candidates: yfinance (already in venv) or manual CSV. Decision required (see `open_questions.md`).
2. `mftool-mcp` / TigZig tool names, schemas, transports, auth unknown → discovery required before MF implementation.
3. No confirmed analyst-estimate MF data source; equity analyst estimates likely via `company-research` (`analyst_research` / `get_analyst_targets`), exact fields TBD.

