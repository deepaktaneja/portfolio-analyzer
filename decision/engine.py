"""Decision engine (Stage 8): combine deterministic rules + optional LLM interpretation.

Rules decide the action (HOLD / MONITOR / TRIM_REVIEW / EXIT_REVIEW /
REPLACEMENT_REVIEW); the LLM only adds contextual interpretation for high-stakes
reviews and never overrides rules (rules retain veto power). Everything runs in
shadow mode — no trades are executed.
"""
from __future__ import annotations

from datetime import UTC, datetime

from decision.config import load_thresholds
from decision.signals import (
    CRITICAL,
    S_CONCENTRATION,
    S_FORWARD_RETURNS,
    S_FUNDAMENTAL,
    S_GOVERNANCE,
    S_RAPID_APPRECIATION,
    S_VALUATION,
    WARN,
    EquitySignalDetector,
    MFSignalDetector,
)

ACTION_HOLD = "HOLD"
ACTION_MONITOR = "MONITOR"
ACTION_TRIM = "TRIM_REVIEW"
ACTION_EXIT = "EXIT_REVIEW"
ACTION_REPLACEMENT = "REPLACEMENT_REVIEW"

_BENIGN = {S_VALUATION, S_RAPID_APPRECIATION, S_FORWARD_RETURNS}
_SEV_WEIGHT = {"INFO": 0.3, "WARN": 0.6, "CRITICAL": 0.85}


class DecisionEngine:
    """Explainable, forward-looking, shadow-mode decision engine."""

    def __init__(self, config: dict | None = None, llm_client=None):
        self.config = config or load_thresholds()
        self.equity = EquitySignalDetector(self.config["equity"])
        self.mf = MFSignalDetector(self.config["mutual_fund"])
        self.engine_cfg = self.config["engine"]
        self.llm = llm_client

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _confidence(signals) -> float:
        if not signals:
            return 0.5
        top = max(_SEV_WEIGHT.get(s.severity, 0.3) for s in signals)
        avg = sum(s.confidence for s in signals) / len(signals)
        return round(min(0.95, (top + avg) / 2), 3)

    @staticmethod
    def _counts(signals) -> dict:
        return {
            "info": sum(1 for s in signals if s.severity == "INFO"),
            "warn": sum(1 for s in signals if s.severity == WARN),
            "critical": sum(1 for s in signals if s.severity == CRITICAL),
        }

    def _decide_equity(self, signals, base_case_cagr: float | None) -> str:
        gov_critical = any(s.signal_type == S_GOVERNANCE and s.severity == CRITICAL for s in signals)
        if gov_critical and self.engine_cfg.get("governance_escalates_independently", True):
            return ACTION_EXIT
        crit = [s for s in signals if s.severity == CRITICAL]
        warn = [s for s in signals if s.severity == WARN]
        if not crit and not warn:
            return ACTION_HOLD
        if not crit:
            # Protect strong compounders from premature selling on benign warnings.
            if (self.engine_cfg.get("protect_compounders", True)
                    and all(s.signal_type in _BENIGN for s in warn)
                    and base_case_cagr is not None
                    and base_case_cagr >= self.engine_cfg.get("compounder_min_growth", 0.15)):
                return ACTION_HOLD
            return ACTION_MONITOR
        crit_types = {s.signal_type for s in crit}
        if len(crit) >= 2 or S_FUNDAMENTAL in crit_types:
            return ACTION_EXIT
        if S_CONCENTRATION in crit_types or S_VALUATION in crit_types:
            return ACTION_TRIM
        return ACTION_MONITOR

    def _needs_llm(self, action: str) -> bool:
        return (bool(self.engine_cfg.get("llm_enabled", True))
                and self.llm is not None
                and action in self.engine_cfg.get("llm_required_for", [ACTION_EXIT, ACTION_REPLACEMENT]))

    def _llm_interpret(self, security_id: str, action: str, signals, research: dict) -> dict | None:
        """Ask the LLM for contextual interpretation (never overrides the rule action)."""
        try:
            from research.llm import sanitise_mcp_content
            sig_txt = "\n".join(f"- [{s.severity}] {s.signal_type}: {s.message}" for s in signals)
            research_txt = sanitise_mcp_content(str({k: research.get(k) for k in (
                "revenue_growth_qoq", "eps_revision", "expected_earnings_growth",
                "management_guidance", "competitive_advantage")}))
            system = (
                "You are a cautious investment analyst. A rule engine flagged this holding for "
                f"{action}. Provide a balanced 2-3 sentence contextual interpretation. Do NOT "
                "invent data, do NOT change the rule action, and note whether the fundamentals "
                "justify caution or whether this may be premature selling."
            )
            user = f"Security: {security_id}\nRule signals:\n{sig_txt}\nFundamental context:\n{research_txt}"
            res = self.llm.generate(system, user)
            return {"provider": res.provider, "model": res.model, "text": res.text, "advisory": True}
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _tiers(signals) -> dict:
        facts = [s.message for s in signals if s.tier == "fact"]
        consensus = [s.message for s in signals if s.tier == "analyst_consensus"]
        ai = [s.message for s in signals if s.tier == "ai_forecast"]
        return {"facts": facts, "analyst_consensus": consensus, "ai_forecasts": ai}

    def evaluate_equity(self, holding: dict, research: dict, base_case_cagr: float | None = None,
                        scenarios: dict | None = None, tax: dict | None = None,
                        replacement_available: bool = False) -> dict:
        """Evaluate one equity holding and return an explainable recommendation dict."""
        signals = self.equity.check_all(holding, research, base_case_cagr)
        action = self._decide_equity(signals, base_case_cagr)
        if action == ACTION_EXIT and replacement_available:
            action = ACTION_REPLACEMENT
        if action in (ACTION_TRIM, ACTION_EXIT, ACTION_REPLACEMENT):
            conf = self._confidence(signals)
            if conf < self.engine_cfg.get("min_confidence_for_review", 0.55):
                action = ACTION_MONITOR
        llm_opinion = None
        if self._needs_llm(action):
            llm_opinion = self._llm_interpret(holding.get("security_id", ""), action, signals, research)

        upside = scenarios.get("upside_cagr") if scenarios else None
        downside = scenarios.get("downside_cagr") if scenarios else None
        reasoning = self._build_reasoning(action, signals, base_case_cagr)
        return {
            "security_id": holding.get("security_id"),
            "security_type": "equity",
            "action": action,
            "confidence": self._confidence(signals),
            "signals": [s.to_dict() for s in signals],
            "signal_counts": self._counts(signals),
            "base_case_cagr": base_case_cagr,
            "upside_cagr": upside,
            "downside_cagr": downside,
            "tax": tax,
            "reasoning": reasoning,
            "tiers": self._tiers(signals),
            "evidence_ids": list(research.get("evidence_ids") or []),
            "llm_opinion": llm_opinion,
            "is_shadow": True,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    def evaluate_mf(self, scheme_metrics: dict, overlap_metrics: dict | None = None) -> dict:
        """Evaluate one mutual fund and return an explainable recommendation dict."""
        signals = self.mf.check_all(scheme_metrics, overlap_metrics)
        crit = [s for s in signals if s.severity == CRITICAL]
        warn = [s for s in signals if s.severity == WARN]
        if not crit and not warn:
            action = ACTION_HOLD
        elif not crit:
            action = ACTION_MONITOR
        elif len(crit) >= 2:
            action = ACTION_REPLACEMENT
        else:
            action = ACTION_TRIM
        if action in (ACTION_TRIM, ACTION_EXIT, ACTION_REPLACEMENT) and (
                self._confidence(signals) < self.engine_cfg.get("min_confidence_for_review", 0.55)):
            action = ACTION_MONITOR
        return {
            "security_id": scheme_metrics.get("scheme_code") or scheme_metrics.get("scheme_name"),
            "security_type": "mf",
            "action": action,
            "confidence": self._confidence(signals),
            "signals": [s.to_dict() for s in signals],
            "signal_counts": self._counts(signals),
            "reasoning": self._build_reasoning(action, signals, None),
            "tiers": self._tiers(signals),
            "llm_opinion": None,
            "is_shadow": True,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _build_reasoning(action: str, signals, base_case_cagr: float | None) -> str:
        if not signals:
            return f"{action}: no material review signals triggered."
        parts = [f"{action}: " + "; ".join(s.message for s in signals)]
        if base_case_cagr is not None:
            parts.append(f"Forward base-case 3y CAGR {base_case_cagr*100:.1f}%.")
        if action == ACTION_HOLD and signals:
            parts.append("Retained despite benign warnings (compounder protection / low severity).")
        return " ".join(parts)

