"""Tests for the decision engine (Stage 8)."""
from __future__ import annotations

from decision.config import load_thresholds
from decision.engine import (
    ACTION_EXIT,
    ACTION_HOLD,
    ACTION_MONITOR,
    ACTION_REPLACEMENT,
    ACTION_TRIM,
    DecisionEngine,
)
from research.llm import LLMResult


def _engine():
    return DecisionEngine(load_thresholds())


def test_healthy_holding_hold():
    r = _engine().evaluate_equity({"security_id": "A", "weight_pct": 5.0}, {}, base_case_cagr=0.20)
    assert r["action"] == ACTION_HOLD
    assert r["is_shadow"] is True


def test_concentrated_trims():
    r = _engine().evaluate_equity({"security_id": "A", "weight_pct": 20.0}, {}, base_case_cagr=0.18)
    assert r["action"] == ACTION_TRIM


def test_governance_escalates_to_exit_independently():
    r = _engine().evaluate_equity({"security_id": "A", "weight_pct": 5.0}, {"auditor_concerns": True}, base_case_cagr=0.30)
    assert r["action"] == ACTION_EXIT  # governance overrides strong growth


def test_compounder_protection():
    # only valuation warning + strong forward growth -> HOLD (protect compounder)
    r = _engine().evaluate_equity({"security_id": "A", "weight_pct": 5.0}, {"forward_pe_percentile": 92}, base_case_cagr=0.20)
    assert r["action"] == ACTION_HOLD
    # same valuation warning but weak growth -> MONITOR
    r = _engine().evaluate_equity({"security_id": "A", "weight_pct": 5.0}, {"forward_pe_percentile": 92}, base_case_cagr=0.05)
    assert r["action"] == ACTION_MONITOR


def test_fundamental_deterioration_exits():
    r = _engine().evaluate_equity(
        {"security_id": "A", "weight_pct": 5.0},
        {"revenue_growth_qoq": -0.10, "eps_revision": -0.18, "margin_change": -0.05},
        base_case_cagr=0.04,
    )
    assert r["action"] == ACTION_EXIT
    assert r["signal_counts"]["critical"] >= 1


def test_replacement_when_available():
    r = _engine().evaluate_equity(
        {"security_id": "A", "weight_pct": 5.0},
        {"auditor_concerns": True}, base_case_cagr=0.10, replacement_available=True,
    )
    assert r["action"] == ACTION_REPLACEMENT


def test_tiers_distinguish_facts_forecasts():
    r = _engine().evaluate_equity(
        {"security_id": "A", "weight_pct": 20.0},
        {"forward_pe_percentile": 90}, base_case_cagr=0.03,
    )
    tiers = r["tiers"]
    assert isinstance(tiers["facts"], list)
    assert isinstance(tiers["analyst_consensus"], list)
    assert isinstance(tiers["ai_forecasts"], list)


def test_upside_downside_from_scenarios():
    r = _engine().evaluate_equity(
        {"security_id": "A", "weight_pct": 20.0}, {},
        scenarios={"upside_cagr": 0.18, "downside_cagr": -0.10},
    )
    assert r["upside_cagr"] == 0.18
    assert r["downside_cagr"] == -0.10


def test_mf_evaluate_hold_and_review():
    r = _engine().evaluate_mf({"scheme_code": "MF1", "analytics": {"excess_3y": 0.02, "sharpe_ratio": 1.0}})
    assert r["action"] == ACTION_HOLD
    r = _engine().evaluate_mf({"scheme_code": "MF2", "analytics": {"excess_3y": -0.06, "sharpe_ratio": -0.3}})
    assert r["action"] in (ACTION_MONITOR, ACTION_TRIM, ACTION_REPLACEMENT)


class _FakeLLM:
    def generate(self, system, user):
        return LLMResult(text="Governance concerns warrant caution.", model="fake", provider="test")


def test_llm_interpretation_only_for_exit():
    eng = DecisionEngine(load_thresholds(), llm_client=_FakeLLM())
    r = eng.evaluate_equity({"security_id": "A", "weight_pct": 5.0}, {"auditor_concerns": True})
    assert r["action"] == ACTION_EXIT
    assert r["llm_opinion"] is not None
    assert r["llm_opinion"]["advisory"] is True


def test_no_llm_means_rule_based():
    r = _engine().evaluate_equity({"security_id": "A", "weight_pct": 5.0}, {"auditor_concerns": True})
    assert r["llm_opinion"] is None
    assert r["action"] == ACTION_EXIT


def test_confidence_bounds():
    r = _engine().evaluate_equity({"security_id": "A", "weight_pct": 20.0}, {})
    assert 0.0 <= r["confidence"] <= 1.0
