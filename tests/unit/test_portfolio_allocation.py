"""Tests for Stage 9 allocation analysis."""
from __future__ import annotations

from portfolio import allocation
from portfolio.config import load_targets

TARGETS = load_targets()


def test_classify_asset_class():
    assert allocation.classify_asset_class("HDFC Gold ETF", is_mf=True) == "Gold"
    assert allocation.classify_asset_class("ICICI Liquid Fund", is_mf=True) == "Debt"
    assert allocation.classify_asset_class("HDFC Balanced Advantage", is_mf=True) == "Hybrid"
    assert allocation.classify_asset_class("Reliance Industries", is_mf=False) == "Equity"


def test_normalize_and_total():
    pos = allocation.normalize_positions([
        {"security_id": "a", "value": 100, "asset_class": "Equity"},
        {"security_id": "b", "value": "300", "name": "Gold Fund", "is_mf": True},
    ])
    assert pos[0]["security_id"] == "A"
    assert allocation.total_value(pos) == 400.0
    assert any(p["asset_class"] == "Gold" for p in pos)


def test_current_allocation_pct():
    pos = allocation.normalize_positions([
        {"security_id": "a", "value": 600, "asset_class": "Equity"},
        {"security_id": "b", "value": 400, "asset_class": "Debt"},
    ])
    alloc = allocation.current_allocation(pos)
    assert alloc["pct"]["Equity"] == 60.0
    assert alloc["pct"]["Debt"] == 40.0


def test_compute_drift():
    drift = allocation.compute_drift({"Equity": 70.0, "Debt": 30.0}, TARGETS)
    assert drift["Equity"] == round(70.0 - 65.0, 2)
    assert drift["Debt"] == round(30.0 - 25.0, 2)


def test_allocation_report_breach():
    pos = allocation.normalize_positions([
        {"security_id": "a", "value": 950, "asset_class": "Equity"},
        {"security_id": "b", "value": 50, "asset_class": "Debt"},
    ])
    rep = allocation.allocation_report(pos, TARGETS)
    assert "Equity" in rep["breached"]
    assert "Debt" in rep["breached"]
    assert rep["total"] == 1000.0


def test_concentration_metrics():
    pos = allocation.normalize_positions([
        {"security_id": "a", "value": 100},
        {"security_id": "b", "value": 100},
    ])
    m = allocation.concentration_metrics(pos)
    assert m["hhi"] == 0.5
    assert m["top_pct"] == 50.0
    assert m["n_positions"] == 2
