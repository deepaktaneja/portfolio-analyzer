"""Stage 12 — deterministic intent routing tests."""
from __future__ import annotations

import pytest

from copilot.intent import classify, extract_percent, parse_amount
from tests.unit.copilot_helpers import make_copilot_ctx


@pytest.mark.parametrize("text,expected", [
    ("5 lakh", 500000.0), ("₹5,00,000", 500000.0), ("2.5 crore", 25000000.0),
    ("50k", 50000.0), ("rebalance with 1,000,000", 1000000.0),
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == pytest.approx(expected)


def test_extract_percent():
    assert extract_percent("trim 25% of RELIANCE") == 25.0
    assert extract_percent("trim RELIANCE") is None


def test_classify_scenarios():
    cc = make_copilot_ctx()
    assert classify("What is my portfolio value and XIRR?", cc).tool == "portfolio_summary"
    assert classify("What is the XIRR of RELIANCE?", cc).tool == "xirr"
    assert classify("Show my asset allocation", cc).tool == "allocation"
    assert classify("Which holdings drive concentration?", cc).tool == "concentration"
    assert classify("Show my unrealized gains", cc).tool == "gains"
    assert classify("Do my mutual funds overlap?", cc).tool == "overlap"
    assert classify("Show my signals", cc).tool == "signals"
    assert classify("Show my top holdings", cc).tool == "holdings"


def test_trim_scenario_extracts_security_and_percent():
    cc = make_copilot_ctx()
    intent = classify("What happens if I trim 25% of RELIANCE?", cc)
    assert intent.tool == "trim_simulation"
    assert intent.args["security"] == "INE001"
    assert intent.args["pct"] == pytest.approx(25.0)


def test_explain_precedes_trim():
    cc = make_copilot_ctx()
    assert classify("Why trim RELIANCE?", cc).tool == "explain_signal"
    assert classify("Explain the EXIT signal for TCS", cc).tool == "explain_signal"


def test_rebalance_extracts_cash():
    cc = make_copilot_ctx()
    intent = classify("How can I rebalance with ₹5 lakh of new investments?", cc)
    assert intent.tool == "rebalance_plan"
    assert intent.args["cash"] == pytest.approx(500000.0)


def test_replacement_needs_two_securities():
    cc = make_copilot_ctx()
    intent = classify("What if I replace RELIANCE with TCS?", cc)
    assert intent.tool == "replacement_simulation"
    assert intent.args["security_a"] == "INE001"
    assert intent.args["security_b"] == "INE002"


def test_research_routing():
    cc = make_copilot_ctx()
    intent = classify("Research RELIANCE for governance and valuation", cc)
    assert intent.tool == "research_report"
    assert intent.args["security"] == "INE001"


def test_default_is_summary():
    cc = make_copilot_ctx()
    assert classify("Hello there", cc).tool == "portfolio_summary"
