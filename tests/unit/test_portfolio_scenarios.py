"""Tests for Stage 9 scenario comparison."""
from __future__ import annotations

from portfolio import scenarios

TGT = {"asset_classes": {"Equity": {"target": 50.0}, "Debt": {"target": 50.0}}}

POS = [
    {"security_id": "EQ1", "name": "Eq", "value": 800, "asset_class": "Equity"},
    {"security_id": "DB1", "name": "Debt", "value": 200, "asset_class": "Debt", "is_mf": True},
]


def test_project_class_values():
    actions = [{"action": "SELL", "asset_class": "Equity", "amount": 300},
               {"action": "BUY", "asset_class": "Debt", "amount": 300}]
    proj = scenarios.project_class_values(POS, actions)
    assert proj["Equity"] == 500.0
    assert proj["Debt"] == 500.0


def test_expected_return():
    r = scenarios.expected_return({"Equity": 50.0, "Debt": 50.0}, {"Equity": 0.12, "Debt": 0.06})
    assert abs(r - 0.09) < 1e-9


def test_compare_scenarios_improves_drift():
    plan = {"actions": [{"action": "SELL", "asset_class": "Equity", "amount": 300},
                        {"action": "BUY", "asset_class": "Debt", "amount": 300}],
            "estimated_tax": 500.0, "sells_value": 300.0, "buys_value": 300.0, "turnover_pct": 30.0}
    res = scenarios.compare_scenarios(POS, TGT, plan, {"Equity": 0.12, "Debt": 0.06})
    assert res["max_abs_drift_after"] < res["max_abs_drift_before"]
    assert res["costs"]["estimated_tax"] == 500.0
    assert res["proposed"]["expected_return"] is not None


def test_compare_three_way():
    plan = {"actions": [{"action": "BUY", "asset_class": "Debt", "amount": 200}],
            "estimated_tax": 0.0, "sells_value": 0.0, "buys_value": 200.0, "turnover_pct": 0.0}
    res = scenarios.compare_three_way(POS, TGT, 200.0, plan, plan_cash_only=plan)
    assert set(res.keys()) >= {"do_nothing", "invest_cash", "rebalance", "cash_available"}
    assert res["cash_available"] == 200.0
