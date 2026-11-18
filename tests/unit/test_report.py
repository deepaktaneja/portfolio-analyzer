"""Tests for structured research report generation.

Tests the ResearchReportBuilder which assembles Markdown reports from
orchestrator output, forecast results, and portfolio context.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from research.report import ResearchReportBuilder, export_report_markdown


# ---------------------------------------------------------------------------
# Markdown report generation tests
# ---------------------------------------------------------------------------

def test_build_markdown_basic_structure():
    orchestrator_output = {
        "report_md": "# Research Report\nSome analysis.",
        "llm_provider": "gemini",
        "llm_model": "gemini-1.5-flash",
        "evidence_timestamps": {"get_financials": "2024-01-01T00:00:00+00:00"},
        "mcp_errors": [],
        "evidence_ids": ["abc123", "def456"],
        "domains_covered": ["financials"],
    }
    forecast = None
    portfolio_ctx = None

    md = ResearchReportBuilder.build_markdown(
        symbol="RELIANCE", isin="INE002A01018",
        orchestrator_output=orchestrator_output,
        forecast=forecast, portfolio_context=portfolio_ctx,
    )

    assert "# Research Report: RELIANCE" in md
    assert "**ISIN:** INE002A01018" in md
    assert "Generated:" in md
    assert "Data Freshness & Sources" in md
    assert "get_financials" in md
    assert "Evidence Citations" in md
    assert "abc123" in md and "def456" in md
    assert "No buy/sell/trim/exit signals" in md


def test_build_markdown_with_forecast():
    orchestrator_output = {"report_md": "", "llm_provider": "none", "llm_model": "none"}
    forecast = {
        "symbol": "RELIANCE",
        "current_price": 2500,
        "base_case_cagr_3y": -0.0276,
        "assumptions": {"revenue_growth_y1": 0.10},
        "data_tier_notes": ["No historical data"],
        "scenarios": [
            {
                "scenario": "bear",
                "pe_multiple": 15,
                "projected_eps_y3": 104.74,
                "estimated_price_y3": 1571.1,
                "current_price": 2500,
                "expected_return_3y_pct": -37.16,
                "expected_return_cagr_pct": -14.34,
            },
            {
                "scenario": "base",
                "pe_multiple": 20,
                "projected_eps_y3": 114.95,
                "estimated_price_y3": 2299.0,
                "current_price": 2500,
                "expected_return_3y_pct": -8.04,
                "expected_return_cagr_pct": -2.76,
            },
            {
                "scenario": "bull",
                "pe_multiple": 25,
                "projected_eps_y3": 125.64,
                "estimated_price_y3": 3141.0,
                "current_price": 2500,
                "expected_return_3y_pct": 25.64,
                "expected_return_cagr_pct": 7.91,
            },
        ],
    }
    portfolio_ctx = None

    md = ResearchReportBuilder.build_markdown(
        symbol="RELIANCE", isin=None,
        orchestrator_output=orchestrator_output,
        forecast=forecast, portfolio_context=portfolio_ctx,
    )

    assert "Forward-Looking Valuation Scenarios" in md
    assert "AI-generated forecasts" in md or "AI Forecast" in md
    assert "| Bear |" in md
    assert "| Base |" in md
    assert "| Bull |" in md
    assert "6.3%" in md or "7.91%" in md


def test_build_markdown_with_portfolio_context():
    orchestrator_output = {"report_md": "", "llm_provider": "none", "llm_model": "none"}
    forecast = None
    portfolio_ctx = {
        "total_invested": 500000,
        "total_current_value": 600000,
        "total_unrealized_gain": 100000,
        "portfolio_xirr": 0.1234,
        "concentration_risk": [
            {"symbol": "HIGHWEIGHT", "weight_pct": 30.0,
             "reason": "concentration > 25%"},
        ],
        "valuation_risk": [],
        "fundamental_risk": [],
    }

    md = ResearchReportBuilder.build_markdown(
        symbol="RELIANCE", isin=None,
        orchestrator_output=orchestrator_output,
        forecast=forecast, portfolio_context=portfolio_ctx,
    )

    assert "Portfolio Context" in md
    assert "Total Invested: Rs 500,000" in md
    assert "Portfolio XIRR: 12.3%" in md
    assert "**HIGHWEIGHT:**" in md
    assert "No buy/sell/trim/exit signals" in md


def test_export_report_markdown():
    content = "# Test Report\n\nThis is a test research report."
    path = export_report_markdown(content, "/tmp/test_research.md")
    assert path == "/tmp/test_research.md"
    assert "Test Report" in open(path).read()
    import os
    os.remove(path)


def test_build_markdown_empty_orchestrator_output():
    """Empty orchestrator output still produces a valid report skeleton."""
    md = ResearchReportBuilder.build_markdown(
        symbol="TCS", isin="INE002B01018",
        orchestrator_output={"report_md": "", "llm_provider": "none", "llm_model": "none"},
        forecast=None, portfolio_context=None,
    )
    assert "# Research Report: TCS" in md
    assert "Data Freshness & Sources" in md
    assert "No evidence IDs recorded." in md


def test_save_to_db_and_snapshot(tmp_path):
    """Research report + snapshot persistence round-trip via isolated research DB."""
    from models.research import (
        get_research_session,
        ResearchReport,
        ResearchSnapshot,
    )

    db_url = f"sqlite:///{tmp_path / 'research_test.sqlite3'}"
    session = get_research_session(db_url=db_url)
    try:
        report_id = ResearchReportBuilder.save_to_db(
            session,
            portfolio_id=1,
            security_id="INE002A01018",
            title="Test report: RELIANCE",
            content_md="# RELIANCE\nContent.",
            evidence_ids=[],
            model="gemini-1.5-flash",
        )
        assert report_id >= 1

        snap_id = ResearchReportBuilder.add_snapshot(
            session,
            report_id=report_id,
            security_id="INE002A01018",
            snapshot_date="2026-11-14",
            key_metrics={"revenue": 900000},
            outlook="base",
        )
        assert snap_id >= 1

        report = session.query(ResearchReport).filter_by(id=report_id).first()
        assert report.security_id == "INE002A01018"
        snapshot = session.query(ResearchSnapshot).filter_by(report_id=report_id).first()
        assert snapshot.outlook == "base"
        assert "revenue" in snapshot.key_metrics_json
    finally:
        session.close()