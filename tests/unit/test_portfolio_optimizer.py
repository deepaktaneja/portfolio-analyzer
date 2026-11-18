"""Tests for Stage 9 rebalancing optimizer & strategies."""
from __future__ import annotations

from portfolio import optimizer, rebalancer
from portfolio.tax_lots import build_fifo_lots

TGT = {
    "asset_classes": {"Equity": {"target": 50.0, "min": 40.0, "max": 60.0},
                      "Debt": {"target": 50.0, "min": 40.0, "max": 60.0}},
    "rebalancing": {"prioritize_cash": True, "min_trade_size": 1000, "max_turnover_pct": 100.0,
                    "prioritize_sip": True, "strategy": "threshold", "drift_tolerance": 5.0},
    "tax": {},
}

# Realistic rupee magnitudes (>= min_trade_size).
POS = [
    {"security_id": "EQ1", "name": "Equity One", "value": 500_000, "asset_class": "Equity", "price": 100},
    {"security_id": "EQ2", "name": "Equity Two", "value": 300_000, "asset_class": "Equity", "price": 100},
    {"security_id": "DB1", "name": "Debt One", "value": 200_000, "asset_class": "Debt", "is_mf": True, "price": 50},
]


def _lots():
    txs = [
        {"isin": "EQ1", "side": "BUY", "trade_date": "2022-01-01", "quantity": 5000, "price": 100},
        {"isin": "EQ2", "side": "BUY", "trade_date": "2022-01-01", "quantity": 3000, "price": 100},
    ]
    return {"EQ1": build_fifo_lots(txs, "EQ1"), "EQ2": build_fifo_lots(txs, "EQ2")}


def test_cash_first_no_sells_when_underweight():
    # Underweight equity & debt; cash funds both without selling.
    pos = [
        {"security_id": "EQ1", "name": "Eq", "value": 200_000, "asset_class": "Equity", "price": 100},
        {"security_id": "DB1", "name": "Debt", "value": 300_000, "asset_class": "Debt", "is_mf": True, "price": 50},
    ]
    plan = optimizer.plan_rebalance(pos, TGT, cash_available=500_000, sell_date="2026-01-01")
    assert plan["sells_value"] == 0.0
    assert plan["cash_used"] > 0


def test_sells_fund_deficit_without_cash():
    plan = optimizer.plan_rebalance(POS, TGT, cash_available=0, lots_by_security=_lots(), sell_date="2026-01-01")
    sells = [a for a in plan["actions"] if a["action"] == "SELL"]
    buys = [a for a in plan["actions"] if a["action"] == "BUY"]
    assert sells and buys
    assert plan["sells_value"] > 0
    assert any(b["asset_class"] == "Debt" for b in buys)


def test_max_turnover_respected():
    tgt = {**TGT, "rebalancing": {**TGT["rebalancing"], "max_turnover_pct": 10.0}}
    plan = optimizer.plan_rebalance(POS, tgt, cash_available=0, lots_by_security=_lots(), sell_date="2026-01-01")
    assert plan["turnover_pct"] <= 10.0001


def test_compounder_protected():
    positions = [
        {"security_id": "EQ_BIG", "name": "Compounder", "value": 800_000, "asset_class": "Equity", "price": 100},
        {"security_id": "DB1", "name": "Debt", "value": 200_000, "asset_class": "Debt", "is_mf": True, "price": 50},
    ]
    lots = {"EQ_BIG": build_fifo_lots(
        [{"isin": "EQ_BIG", "side": "BUY", "trade_date": "2022-01-01", "quantity": 8000, "price": 100}], "EQ_BIG")}
    plan = optimizer.plan_rebalance(positions, TGT, cash_available=0, compounders={"EQ_BIG"},
                                    lots_by_security=lots, sell_date="2026-01-01")
    assert "EQ_BIG" in plan["protected_compounders_skipped"]
    assert plan["sells_value"] == 0.0


def test_missing_cost_basis_flag():
    plan = optimizer.plan_rebalance(POS, TGT, cash_available=0, lots_by_security={}, sell_date="2026-01-01")
    assert plan["missing_cost_basis"] is True


def test_should_rebalance_threshold():
    rep = {"rows": [{"asset_class": "Equity", "drift_pct": 12.0}], "breached": []}
    assert rebalancer.should_rebalance("threshold", rep, 5.0)["due"] is True
    rep2 = {"rows": [{"asset_class": "Equity", "drift_pct": 2.0}], "breached": []}
    assert rebalancer.should_rebalance("threshold", rep2, 5.0)["due"] is False


def test_should_rebalance_annual():
    res = rebalancer.should_rebalance("annual", {"rows": [], "breached": []}, 5.0, last_rebalance_date="2025-01-01", today="2026-06-01")
    assert res["due"] is True
    res2 = rebalancer.should_rebalance("annual", {"rows": [], "breached": []}, 5.0, last_rebalance_date="2026-05-01", today="2026-06-01")
    assert res2["due"] is False


def test_build_execution_plan():
    res = rebalancer.build_execution_plan(POS, TGT, cash_available=300_000, lots_by_security=_lots(), sell_date="2026-01-01")
    assert "allocation" in res and "plan" in res
    assert res["plan"]["cash_used"] > 0

