"""AI research orchestrator: selectively calls MCP tools to analyse a holding.

Orchestrates MCP-based evidence gathering (financials, quarterly results,
analyst targets, guidance, peer comparison, shareholding, disclosures,
prices) and feeds it to the LLM for research narrative generation.

Distinguishes three tiers of information:
  - Historical facts: from MCP server responses (evidence-cited)
  - Analyst consensus: from MCP analyst-estimate tools (evidence-cited)
  - AI-generated forecasts: from the LLM, clearly labelled as such

LLM is used for research/interpretation only. It never alters accounting
figures or generates sell/trim signals (reserved for Stage 8).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from mcp_integration.adapter import MCPAdapter
from research.llm import LLMClient, sanitise_mcp_content

# MCP tools we selectively call, grouped by analysis domain.
TOOL_DOMAINS = {
    "financials": ["get_financials", "get_financial_ratios"],
    "quarterly": ["get_quarterly_results"],
    "analyst": ["get_analyst_targets", "get_recent_news"],
    "guidance": ["get_forward_outlook", "get_management_guidance"],
    "peer": ["get_peer_comparison", "compare_companies"],
    "governance": ["get_shareholding_pattern", "analyze_red_flags", "get_corporate_actions"],
    "prices": ["get_stock_history", "get_chart"],
}

# Default tool arguments template per security.
DEFAULT_ARGS = {
    "get_financials": lambda symbol: {"symbol": symbol, "period": "annual"},
    "get_financial_ratios": lambda symbol: {"symbol": symbol, "period": "annual"},
    "get_quarterly_results": lambda symbol: {"symbol": symbol, "period": "quarterly", "limit": 8},
    "get_analyst_targets": lambda symbol: {"symbol": symbol},
    "get_recent_news": lambda symbol: {"symbol": symbol, "limit": 10},
    "get_forward_outlook": lambda symbol: {"symbol": symbol},
    "get_management_guidance": lambda symbol: {"symbol": symbol},
    "get_peer_comparison": lambda symbol: {"symbol": symbol, "peers": []},
    "compare_companies": lambda symbol: {"symbol": symbol},
    "get_shareholding_pattern": lambda symbol: {"symbol": symbol},
    "analyze_red_flags": lambda symbol: {"symbol": symbol},
    "get_corporate_actions": lambda symbol: {"symbol": symbol},
    "get_stock_history": lambda symbol: {"symbol": symbol, "period": "1y"},
    "get_chart": lambda symbol: {"symbol": symbol, "period": "1y"},
}


class ResearchContext:
    """Accumulated research evidence for a single security."""

    def __init__(self, symbol: str, isin: str | None = None):
        self.symbol = symbol
        self.isin = isin
        self.facts: dict[str, Any] = {}
        self.analyst_consensus: dict[str, Any] = {}
        self.prices: dict[str, Any] = {}
        self.governance: dict[str, Any] = {}
        self.errors: list[str] = []
        self.evidence_ids: list[str] = []
        self.timestamps: dict[str, str] = {}

    def add_mcp_result(
        self,
        domain: str,
        tool: str,
        envelope: dict[str, Any],
    ) -> None:
        """Merge an MCP evidence envelope into the appropriate context bucket."""
        data = envelope.get("data", {}) or {}
        status = envelope.get("status", "unknown")
        eid = envelope.get("evidence_id", "")
        retrieved_at = envelope.get("retrieved_at", "")

        if status == "error" or (envelope.get("partial") and not data):
            self.errors.append(
                f"{tool}: {envelope.get('warnings', ['unknown error'])[0] if envelope.get('warnings') else 'unknown error'}"
            )
            return

        self.evidence_ids.append(eid)
        self.timestamps[tool] = retrieved_at

        if domain == "financials" or domain == "quarterly":
            self.facts[tool] = data
        elif domain == "analyst":
            self.analyst_consensus[tool] = data
        elif domain == "guidance" or domain == "peer":
            self.facts[tool] = data
        elif domain == "governance":
            self.governance[tool] = data
        elif domain == "prices":
            self.prices[tool] = data


class ResearchOrchestrator:
    """Selectively calls MCP tools and orchestrates LLM research generation."""

    def __init__(
        self,
        adapter: MCPAdapter | None = None,
        llm: LLMClient | None = None,
    ):
        self.adapter = adapter or MCPAdapter()
        self.llm = llm or LLMClient()

    async def research_security(
        self,
        symbol: str,
        isin: str | None = None,
        domains: list[str] | None = None,
    ) -> ResearchContext:
        """Run the full MCP evidence-gathering pipeline for one security."""
        if domains is None:
            domains = list(TOOL_DOMAINS.keys())

        ctx = ResearchContext(symbol=symbol, isin=isin)

        for domain in domains:
            tools = TOOL_DOMAINS.get(domain, [])
            for tool in tools:
                args = DEFAULT_ARGS.get(tool, lambda s: {"symbol": s})(symbol)
                try:
                    envelope = await self.adapter.call_tool(
                        server="company-research",
                        tool=tool,
                        arguments=args,
                    )
                    ctx.add_mcp_result(domain, tool, envelope)
                except ValueError as exc:
                    ctx.errors.append(f"{tool}: {exc}")
                except Exception as exc:  # noqa: BLE001
                    ctx.errors.append(f"{tool}: unexpected error: {exc}")

        return ctx

    def build_llm_prompt(
        self,
        ctx: ResearchContext,
        portfolio_context: dict[str, Any] | None = None,
        include_forecast: bool = True,
    ) -> tuple[str, str]:
        """Build system + user prompts for the LLM research narrative."""
        system_prompt = (
            "You are an investment research analyst covering Indian equities. "
            "Your task is to write a structured research report based ONLY on "
            "the evidence provided below. "
            "Rules: "
            "(1) Do not invent financial data, analyst estimates, or price information. "
            "(2) Distinguish clearly between HISTORICAL FACTS (from MCP servers), "
            "ANALYST CONSENSUS (from analyst-estimate tools), and AI-GENERATED FORECASTS "
            "(your own projections, clearly labelled as such). "
            "(3) Cite evidence by tool name and retrieval timestamp. "
            "(4) Do not generate buy/sell/trim/exit signals — those are reserved for a later stage. "
            "(5) Keep the tone objective and analytical. "
            "(6) All figures are in INR unless otherwise stated."
        )

        user_parts = [f"Security: {ctx.symbol}" + (f" (ISIN: {ctx.isin})" if ctx.isin else "")]

        if ctx.facts:
            user_parts.append("\n--- HISTORICAL FACTS (MCP evidence) ---")
            for tool, data in ctx.facts.items():
                safe = sanitise_mcp_content(json.dumps(data, indent=2, default=str)[:2000])
                user_parts.append(f"\n[{tool}] retrieved {ctx.timestamps.get(tool, 'unknown')}:\n{safe}")

        if ctx.analyst_consensus:
            user_parts.append("\n--- ANALYST CONSENSUS (MCP evidence) ---")
            for tool, data in ctx.analyst_consensus.items():
                safe = sanitise_mcp_content(json.dumps(data, indent=2, default=str)[:2000])
                user_parts.append(f"\n[{tool}] retrieved {ctx.timestamps.get(tool, 'unknown')}:\n{safe}")

        if ctx.prices:
            user_parts.append("\n--- PRICE HISTORY (MCP evidence) ---")
            for tool, data in ctx.prices.items():
                safe = sanitise_mcp_content(json.dumps(data, indent=2, default=str)[:1500])
                user_parts.append(f"\n[{tool}] retrieved {ctx.timestamps.get(tool, 'unknown')}:\n{safe}")

        if ctx.governance:
            user_parts.append("\n--- GOVERNANCE & DISCLOSURES (MCP evidence) ---")
            for tool, data in ctx.governance.items():
                safe = sanitise_mcp_content(json.dumps(data, indent=2, default=str)[:1500])
                user_parts.append(f"\n[{tool}] retrieved {ctx.timestamps.get(tool, 'unknown')}:\n{safe}")

        if portfolio_context:
            user_parts.append("\n--- PORTFOLIO CONTEXT (internal accounting, not LLM-computable) ---")
            for k, v in portfolio_context.items():
                user_parts.append(f"{k}: {v}")

        if ctx.errors:
            user_parts.append("\n--- DATA GAPS ---")
            for err in ctx.errors:
                user_parts.append(f"- {err}")

        if include_forecast:
            user_parts.append(
                "\n--- TASK ---\n"
                "Write a research report with these sections: "
                "1. Company Overview, 2. Financial Performance (historical), "
                "3. Quarterly Trends, 4. Analyst Consensus, 5. Business Quality & Governance, "
                "6. Forward-Looking Outlook (1-3 years, clearly labelled AI-generated), "
                "7. Valuation Scenarios (bear/base/bull, with transparent assumptions), "
                "8. Portfolio Impact & Risks, 9. Key Risks & Data Gaps. "
                "For every factual claim, cite the source tool and timestamp. "
                "Label all AI-generated forecasts explicitly as 'AI FORECAST (not analyst consensus)'."
            )

        user_prompt = "\n".join(user_parts)
        return system_prompt, user_prompt

    async def generate_report(
        self,
        symbol: str,
        isin: str | None = None,
        portfolio_context: dict[str, Any] | None = None,
        domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run the full research pipeline: MCP evidence -> LLM report."""
        ctx = await self.research_security(symbol=symbol, isin=isin, domains=domains)
        system_prompt, user_prompt = self.build_llm_prompt(
            ctx, portfolio_context=portfolio_context, include_forecast=True
        )
        llm_result = self.llm.generate(system_prompt, user_prompt)

        report = {
            "symbol": symbol,
            "isin": isin,
            "generated_at": datetime.now(UTC).isoformat(),
            "llm_provider": llm_result.provider,
            "llm_model": llm_result.model,
            "report_md": llm_result.text,
            "evidence_ids": ctx.evidence_ids,
            "evidence_timestamps": ctx.timestamps,
            "mcp_errors": ctx.errors,
            "domains_covered": domains or list(TOOL_DOMAINS.keys()),
            "is_llm_available": llm_result.provider != "none",
        }
        return report