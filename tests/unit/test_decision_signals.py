"""Tests for equity & MF signal detectors (Stage 8)."""
from __future__ import annotations

from decision.config import load_thresholds
from decision.signals import (
    CRITICAL,
    S_CONCENTRATION,
    S_EXPENSES,
    S_FORWARD_RETURNS,
    S_FUNDAMENTAL,
    S_GOVERNANCE,
    S_MANAGER,
    S_MF_CONCENTRATION,
    S_OVERLAP,
    S_RAPID_APPRECIATION,
    S_UNDERPERFORMANCE,
    S_VALUATION,
    WARN,
    EquitySignalDetector,
    MFSignalDetector,
)

CFG = load_thresholds()
EQ = EquitySignalDetector(CFG["equity"])
MF = MFSignalDetector(CFG["mutual_fund"])


def test_concentration_flag():
    sigs = EQ.check_concentration({"security_id": "A", "weight_pct": 13.0})
    assert any(s.signal_type == S_CONCENTRATION and s.severity == WARN for s in sigs)
    # very concentrated -> critical
    sigs = EQ.check_concentration({"security_id": "A", "weight_pct": 20.0})
    assert any(s.severity == CRITICAL for s in sigs)


def test_concentration_below_threshold_no_signal():
    assert EQ.check_concentration({"security_id": "A", "weight_pct": 5.0}) == []


def test_valuation_signals():
    sigs = EQ.check_valuation({"forward_pe_percentile": 95, "peg": 2.5})
    types = {s.signal_type for s in sigs}
    assert S_VALUATION in types
    assert len(sigs) >= 2


def test_rapid_appreciation_without_earnings_upgrade():
    sigs = EQ.check_rapid_appreciation({"security_id": "A", "price_gain_6m": 0.9}, {"eps_upgrade_6m": 0.02})
    assert any(s.signal_type == S_RAPID_APPRECIATION for s in sigs)


def test_rapid_appreciation_justified_no_signal():
    sigs = EQ.check_rapid_appreciation({"security_id": "A", "price_gain_6m": 0.9}, {"eps_upgrade_6m": 0.2})
    assert sigs == []


def test_fundamental_deterioration():
    sigs = EQ.check_fundamental({"revenue_growth_qoq": -0.08, "eps_revision": -0.12, "margin_change": -0.03, "fcf_change": -0.2})
    assert len(sigs) >= 2
    assert all(s.signal_type == S_FUNDAMENTAL for s in sigs)


def test_governance_auditor_and_pledging():
    sigs = EQ.check_governance({"promoter_pledging": 0.25, "auditor_concerns": True})
    assert any(s.signal_type == S_GOVERNANCE and s.severity == CRITICAL for s in sigs)
    assert len(sigs) >= 2


def test_forward_returns_signal():
    sigs = EQ.check_forward_returns(0.03, {"security_id": "A"})
    assert any(s.signal_type == S_FORWARD_RETURNS for s in sigs)
    assert EQ.check_forward_returns(0.15, {"security_id": "A"}) == []


def test_check_all_aggregates():
    sigs = EQ.check_all(
        {"security_id": "A", "weight_pct": 20.0, "price_gain_6m": 0.9},
        {"forward_pe_percentile": 90, "auditor_concerns": True},
        base_case_cagr=0.03,
    )
    types = {s.signal_type for s in sigs}
    assert S_CONCENTRATION in types
    assert S_GOVERNANCE in types
    assert S_FORWARD_RETURNS in types
    assert all(s.security_id == "A" for s in sigs)


def test_mf_underperformance_and_risk():
    sigs = MF.check_underperformance({"excess_3y": -0.06}, "MF1")
    assert any(s.signal_type == S_UNDERPERFORMANCE for s in sigs)
    sigs = MF.check_risk_adjusted({"sharpe_ratio": -0.2, "performance_consistency_1y": 0.2}, "MF1")
    assert len(sigs) >= 1


def test_mf_overlap_expenses_manager_concentration():
    assert any(s.signal_type == S_OVERLAP for s in MF.check_overlap(35.0, 0.5, "MF1"))
    assert any(s.signal_type == S_EXPENSES for s in MF.check_expenses(1.8, "MF1"))
    assert any(s.signal_type == S_MANAGER for s in MF.check_manager(3, "MF1"))
    assert any(s.signal_type == S_MF_CONCENTRATION for s in MF.check_concentration(60.0, 20.0, "MF1"))


def test_mf_check_all():
    sigs = MF.check_all(
        {"scheme_code": "MF1", "analytics": {"excess_3y": -0.06, "sharpe_ratio": -0.3},
         "expense_ratio": 1.8, "manager_changes": 3},
        {"weight_overlap_pct": 40.0, "jaccard": 0.5},
    )
    types = {s.signal_type for s in sigs}
    assert S_UNDERPERFORMANCE in types
    assert S_OVERLAP in types
    assert S_EXPENSES in types
    assert all(s.security_type == "mf" for s in sigs)


def test_signal_to_dict():
    s = EQ.check_concentration({"security_id": "A", "weight_pct": 20.0})[0]
    d = s.to_dict()
    assert d["signal_type"] == S_CONCENTRATION
    assert "evidence" in d and "thresholds" in d
