"""Tests for MF overlap, look-through exposure & concentration (Stage 7)."""
from __future__ import annotations

from mf import overlap

HOLDS = {
    "A": [
        {"stock_isin": "INE1", "stock_name": "X", "weight_pct": 10, "sector": "IT", "market_cap": "Large"},
        {"stock_isin": "INE2", "stock_name": "Y", "weight_pct": 5, "sector": "Banks", "market_cap": "Large"},
    ],
    "B": [
        {"stock_isin": "INE1", "stock_name": "X", "weight_pct": 8, "sector": "IT", "market_cap": "Large"},
        {"stock_isin": "INE3", "stock_name": "Z", "weight_pct": 4, "sector": "Auto", "market_cap": "Mid"},
    ],
}


def test_pairwise_overlap():
    res = overlap.pairwise_overlap(HOLDS["A"], HOLDS["B"])
    assert res["common_isins"] == ["INE1"]
    assert res["common_count"] == 1
    assert res["weight_overlap_pct"] == 8.0  # min(10, 8)
    assert abs(res["jaccard"] - (1 / 3)) < 1e-3


def test_overlap_matrix_and_flag():
    m = overlap.overlap_matrix(HOLDS)
    assert len(m["pairs"]) == 1
    flagged = overlap.flag_overlap(m["pairs"], weight_threshold=25.0, jaccard_threshold=0.4)
    # 8% weight overlap, jaccard 0.33 -> below both thresholds -> not flagged
    assert flagged == []


def test_flag_overlap_triggers():
    pairs = [{"scheme_a": "A", "scheme_b": "B", "weight_overlap_pct": 30.0, "jaccard": 0.5}]
    assert len(overlap.flag_overlap(pairs)) == 1


def test_look_through_exposure():
    exp = overlap.look_through_exposure({"A": 60.0, "B": 40.0}, HOLDS)
    # INE1: 60*10/100 + 40*8/100 = 6 + 3.2 = 9.2
    assert abs(exp["INE1"]["indirect_pct"] - 9.2) < 1e-6
    assert exp["INE1"]["stock_name"] == "X"


def test_combine_with_direct_equity():
    exp = overlap.look_through_exposure({"A": 100.0}, HOLDS)
    combined = overlap.combine_with_direct_equity(exp, {"INE1": 15.0})
    top = combined[0]
    assert top["stock_isin"] == "INE1"
    assert top["direct_pct"] == 15.0
    assert abs(top["total_pct"] - (top["direct_pct"] + top["indirect_pct"])) < 1e-6


def test_sector_and_marketcap_concentration():
    exp = overlap.look_through_exposure({"A": 100.0}, HOLDS)
    combined = overlap.combine_with_direct_equity(exp, {})
    sec = overlap.sector_concentration(combined)
    assert "IT" in sec["buckets"]
    mc = overlap.market_cap_concentration(combined)
    assert "Large" in mc["buckets"] or "Mid" in mc["buckets"]


def test_asset_class_concentration():
    res = overlap.asset_class_concentration({"A": 60.0, "B": 40.0}, {"A": "Equity", "B": "Debt"})
    assert res["buckets"]["Equity"] == 60.0
    assert res["buckets"]["Debt"] == 40.0


def test_disclosure_status_labels():
    holdings = {"A": HOLDS["A"], "B": []}
    as_of = {"A": "2026-11-01", "B": None}
    rows = overlap.disclosure_status(holdings, as_of, max_age_days=45)
    status = {r["scheme_code"]: r["status"] for r in rows}
    assert status["A"] in ("ok", "outdated")
    assert status["B"] == "missing"
