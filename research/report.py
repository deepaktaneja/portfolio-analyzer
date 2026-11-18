"""Structured research report generation with source citations and data timestamps.

Generates Markdown reports from orchestrator output, forecast results, and
portfolio context. Reports can be stored (via models.research.ResearchReport)
and exported to Markdown/HTML/text.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from models.research import ResearchReport, ResearchSnapshot


class ResearchReportBuilder:
    """Builds a structured Markdown research report from research context."""

    @staticmethod
    def build_markdown(
        *,
        symbol: str,
        isin: str | None,
        orchestrator_output: dict[str, Any],
        forecast: dict[str, Any] | None = None,
        portfolio_context: dict[str, Any] | None = None,
    ) -> str:
        """Build a complete Markdown research report."""
        lines: list[str] = []

        lines.append(f"# Research Report: {symbol}")
        if isin:
            lines.append(f"**ISIN:** {isin}")
        lines.append(f"**Generated:** {datetime.now(UTC).isoformat()} UTC")
        lines.append(f"**LLM Provider:** {orchestrator_output.get('llm_provider', 'unknown')}")
        lines.append(f"**LLM Model:** {orchestrator_output.get('llm_model', 'unknown')}")
        lines.append("")

        lines.append("## Data Freshness & Sources")
        timestamps = orchestrator_output.get("evidence_timestamps", {})
        if timestamps:
            lines.append("| MCP Tool | Retrieved At |")
            lines.append("|----------|-------------|")
            for tool, ts in sorted(timestamps.items()):
                lines.append(f"| {tool} | {ts} |")
        else:
            lines.append("*No MCP evidence timestamps available.*")
        lines.append("")

        mcp_errors = orchestrator_output.get("mcp_errors", [])
        if mcp_errors:
            lines.append("## Data Gaps / MCP Errors")
            for err in mcp_errors:
                lines.append(f"- {err}")
            lines.append("")

        report_md = orchestrator_output.get("report_md", "")
        if report_md:
            lines.append("## Research Analysis")
            lines.append(report_md)
        else:
            lines.append("## Research Analysis")
            lines.append("*No LLM report generated (LLM provider unavailable).*")

        if forecast:
            lines.append("")
            lines.append("## Forward-Looking Valuation Scenarios (AI Forecast)")
            lines.append("*All projections below are AI-generated forecasts, not analyst consensus.*")
            lines.append("")
            lines.append("| Scenario | P/E Multiple | EPS (Y3) | Est. Price (Y3) | 3Y Return | CAGR |")
            lines.append("|----------|-------------|----------|-----------------|-----------|------|")
            for sc in forecast.get("scenarios", []):
                lines.append(
                    f"| {sc['scenario'].title()} | {sc['pe_multiple']} | {sc['projected_eps_y3']} | "
                    f"{sc['estimated_price_y3']} | {sc['expected_return_3y_pct']}% | {sc['expected_return_cagr_pct']}% |"
                )
            lines.append("")
            lines.append("**Assumptions:**")
            for k, v in forecast.get("assumptions", {}).items():
                lines.append(f"- {k}: {v}")
            lines.append("")
            for note in forecast.get("data_tier_notes", []):
                lines.append(f"- {note}")

        if portfolio_context:
            lines.append("")
            lines.append("## Portfolio Context")
            lines.append(f"- Total Invested: Rs {portfolio_context.get('total_invested', 0):,.0f}")
            lines.append(f"- Total Current Value: Rs {portfolio_context.get('total_current_value', 0):,.0f}")
            lines.append(f"- Total Unrealized Gain: Rs {portfolio_context.get('total_unrealized_gain', 0):,.0f}")
            if portfolio_context.get("portfolio_xirr") is not None:
                lines.append(f"- Portfolio XIRR: {portfolio_context['portfolio_xirr']:.1%}")
            lines.append("")
            if portfolio_context.get("concentration_risk"):
                lines.append("### Concentration Risks")
                for r in portfolio_context["concentration_risk"]:
                    lines.append(f"- **{r['symbol']}:** {r['reason']}")
                lines.append("")
            if portfolio_context.get("valuation_risk"):
                lines.append("### Valuation Risks")
                for r in portfolio_context["valuation_risk"]:
                    lines.append(f"- **{r['symbol']}:** {r['reason']}")
                lines.append("")
            if portfolio_context.get("fundamental_risk"):
                lines.append("### Fundamental Risks")
                for r in portfolio_context["fundamental_risk"]:
                    lines.append(f"- **{r['symbol']}:** {r['reason']}")
                lines.append("")

        lines.append("## Evidence Citations")
        evidence_ids = orchestrator_output.get("evidence_ids", [])
        if evidence_ids:
            lines.append("The following MCP evidence envelopes were cited in this report:")
            for eid in evidence_ids:
                lines.append(f"- `{eid}`")
        else:
            lines.append("*No evidence IDs recorded.*")
        lines.append("")
        lines.append("> **Disclaimer:** This report is for informational purposes only. AI-generated forecasts are not "
        "analyst consensus and should not be treated as investment advice. No buy/sell/trim/exit signals "
        "are generated in this stage.")

        return "\n".join(lines)

    @staticmethod
    def save_to_db(
        session,
        *,
        portfolio_id: int | None,
        security_id: str | None,
        title: str,
        content_md: str,
        evidence_ids: list[str],
        model: str,
    ) -> int:
        """Persist a research report to the database. Returns the report id."""
        import json
        report = ResearchReport(
            portfolio_id=portfolio_id,
            security_id=security_id,
            title=title,
            content_md=content_md,
            evidence_ids=json.dumps(evidence_ids),
            model=model,
            generated_at=datetime.now(UTC).isoformat(),
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        return int(report.id)

    @staticmethod
    def add_snapshot(
        session,
        *,
        report_id: int,
        security_id: str,
        snapshot_date: str,
        key_metrics: dict[str, Any],
        outlook: str,
    ) -> int:
        """Add a historical snapshot to track changes in outlook over time."""
        import json
        snapshot = ResearchSnapshot(
            report_id=report_id,
            security_id=security_id,
            snapshot_date=snapshot_date,
            key_metrics_json=json.dumps(key_metrics),
            outlook=outlook,
            generated_at=datetime.now(UTC).isoformat(),
        )
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return int(snapshot.id)


def export_report_markdown(content_md: str, filename: str) -> str:
    """Export a research report to a Markdown file. Returns the file path."""
    from pathlib import Path
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content_md, encoding="utf-8")
    return str(path)