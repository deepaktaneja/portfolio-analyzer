"""Tests for explainable MF quality assessment (Stage 7)."""
from __future__ import annotations

from mf.quality import assess_fund_quality


def test_quality_strong_fund():
    q = assess_fund_quality({
        "scheme_code": "A", "scheme_name": "Fund A",
        "excess_3y": 0.03, "sharpe_ratio": 1.2, "performance_consistency_1y": 0.7,
        "expense_ratio": 0.4, "overlap_max": 5.0, "manager_changes": 0,
    })
    assert q["rating"] == "Strong"
    assert q["overall_score"] >= 75
    assert q["requires_review"] is False
    assert q["disclaimer"].startswith("Advisory")


def test_quality_weak_fund_flags_review():
    q = assess_fund_quality({
        "scheme_code": "B",
        "excess_3y": -0.05, "sharpe_ratio": -0.3, "performance_consistency_1y": 0.2,
        "expense_ratio": 1.9, "overlap_max": 45.0, "manager_changes": 3,
    })
    assert q["rating"] in ("Watch", "Review")
    assert q["requires_review"] is True
    assert len(q["flags"]) >= 2
    assert any("expense" in f.lower() for f in q["flags"])


def test_quality_missing_data_is_deterministic():
    q1 = assess_fund_quality({"scheme_code": "C"})
    q2 = assess_fund_quality({"scheme_code": "C"})
    assert q1 == q2
    assert q1["rating"] == "Insufficient data"
    assert q1["overall_score"] is None


def test_quality_components_explainability():
    q = assess_fund_quality({"excess_3y": 0.01, "sharpe_ratio": 0.5})
    assert "excess" in q["components"]
    assert all(isinstance(c["reason"], str) for c in q["components"].values())
    assert len(q["reasons"]) >= 1
