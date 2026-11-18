"""Stage 12 — copilot tools must reconcile with the deterministic engines."""
from __future__ import annotations

import pytest

from copilot.tools import get_tool
from tests.unit.copilot_helpers import make_copilot_ctx


def _run(name, args, ctx):
    spec = get_tool(name)
    assert spec is not None, f"tool {name} not registered"
    return spec.run(args, ctx)


def test_xirr_tool_matches_portfolio_engine():
    from performance.xirr import calculate_portfolio_xirr
    cc = make_copilot_ctx()
    expected = calculate_portfolio_xirr(cc.txs, end_date=cc.end_date, price_lookup=cc.price_lookup)["xirr"]
    res = _run("xirr", {}, cc)
    assert res.ok
    assert res.data["xirr"] == pytest.approx(expected)


def test_xirr_tool_per_security_matches_engine():
    from performance.xirr import calculate_stock_xirr
    cc = make_copilot_ctx()
    expected = calculate_stock_xirr(cc.txs, security_id="INE001", end_date=cc.end_date,
                                    price_lookup=cc.price_lookup)["xirr"]
    res = _run("xirr", {"security": "RELIANCE"}, cc)
    assert res.data["security_id"] == "INE001"
    assert res.data["xirr"] == pytest.approx(expected)


def test_allocation_tool_matches_engine():
    from portfolio import allocation
    cc = make_copilot_ctx()
    rep = allocation.allocation_report(cc.positions, cc.targets)
    res = _run("allocation", {}, cc)
    by_class = {r["asset_class"]: r for r in rep["rows"]}
    for row in res.data["rows"]:
        assert row["current_pct"] == pytest.approx(by_class[row["asset_class"]]["current_pct"])


def test_concentration_tool_matches_engine():
    from portfolio import allocation
    cc = make_copilot_ctx()
    m = allocation.concentration_metrics(cc.positions)
    res = _run("concentration", {}, cc)
    assert res.data["hhi"] == pytest.approx(m["hhi"])
    assert res.data["top_pct"] == pytest.approx(m["top_pct"])


def test_trim_simulation_math():
    cc = make_copilot_ctx()
    res = _run("trim_simulation", {"security": "RELIANCE", "pct": 25}, cc)
    assert res.ok
    assert res.data["trim_proceeds"] == pytest.approx(150000.0)
    # 600k -> 450k of a 1,000k portfolio => 45%
    assert res.data["weight_after_pct"] == pytest.approx(45.0)
    assert res.data["weight_before_pct"] == pytest.approx(60.0)


def test_rebalance_plan_matches_engine():
    from copilot.tools.rebalancing import _lots_by_security
    from portfolio.rebalancer import build_execution_plan
    cc = make_copilot_ctx()
    exp = build_execution_plan(cc.positions, cc.targets, cash_available=500000,
                               lots_by_security=_lots_by_security(cc), sell_date=str(cc.end_date))
    res = _run("rebalance_plan", {"cash": 500000}, cc)
    assert res.ok
    assert res.data["sells_value"] == pytest.approx(exp["plan"]["sells_value"])
    assert res.data["estimated_tax"] == pytest.approx(exp["plan"]["estimated_tax"])


def test_replacement_simulation_uses_engine_and_computes_xirr():
    cc = make_copilot_ctx()
    res = _run("replacement_simulation", {"security_a": "RELIANCE", "security_b": "TCS"}, cc)
    assert res.ok
    assert res.data["actual_xirr"] is not None  # equity txs normalised for the engine
    assert "xirr_difference" in res.data


def test_signals_reconcile_with_decision_engine():
    from copilot.tools.signals import build_recommendations
    cc = make_copilot_ctx()
    recs = build_recommendations(cc)
    res = _run("signals", {}, cc)
    assert res.ok
    assert len(res.data["recommendations"]) == len(recs)
    assert sum(res.data["counts_by_action"].values()) == len(recs)


def test_explain_signal_returns_evidence():
    cc = make_copilot_ctx()
    res = _run("explain_signal", {"security": "RELIANCE"}, cc)
    assert res.ok
    assert res.data["security_id"] == "INE001"
    assert res.data["action"] in ("HOLD", "MONITOR", "TRIM_REVIEW", "EXIT_REVIEW", "REPLACEMENT_REVIEW")


def test_gains_and_holdings():
    cc = make_copilot_ctx()
    g = _run("gains", {}, cc)
    assert g.data["unrealized_total"] == pytest.approx(300000.0)  # 600-400 + 400-300 (in k)
    h = _run("holdings", {"limit": 1}, cc)
    assert len(h.data["holdings"]) == 1
    assert h.data["holdings"][0]["security_id"] == "INE001"


def test_unknown_security_is_rejected():
    cc = make_copilot_ctx()
    res = _run("xirr", {"security": "NOPE"}, cc)
    assert res.ok is False
    assert any("Unknown security" in w for w in res.warnings)
