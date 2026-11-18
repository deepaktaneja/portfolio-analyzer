"""Configurable equity & mutual-fund signal detectors (Stage 8).

Pure, deterministic detectors operating on plain metric dicts. Thresholds come
from :mod:`decision.config` (user-editable YAML). Signals are *advisory* — they
never execute trades and are only combined into a recommendation by the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field

INFO, WARN, CRITICAL = "INFO", "WARN", "CRITICAL"

S_CONCENTRATION = "CONCENTRATION"
S_VALUATION = "VALUATION"
S_RAPID_APPRECIATION = "RAPID_APPRECIATION"
S_FUNDAMENTAL = "FUNDAMENTAL_DETERIORATION"
S_GOVERNANCE = "GOVERNANCE"
S_FORWARD_RETURNS = "LOW_FORWARD_RETURNS"
S_UNDERPERFORMANCE = "BENCHMARK_UNDERPERFORMANCE"
S_RISK_ADJUSTED = "RISK_ADJUSTED_DETERIORATION"
S_OVERLAP = "EXCESSIVE_OVERLAP"
S_EXPENSES = "HIGH_EXPENSES"
S_MANAGER = "MANAGER_OR_PROCESS_CHANGE"
S_MF_CONCENTRATION = "MF_CONCENTRATION"


@dataclass
class Signal:
    """One explainable signal with evidence and the threshold that triggered it."""

    signal_type: str
    severity: str
    confidence: float
    message: str
    evidence: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    current_value: float | None = None
    threshold_value: float | None = None
    security_id: str | None = None
    security_type: str = "equity"
    tier: str = "fact"  # fact | analyst_consensus | ai_forecast

    def to_dict(self) -> dict:
        return {
            "signal_type": self.signal_type,
            "severity": self.severity,
            "confidence": round(float(self.confidence), 3),
            "message": self.message,
            "evidence": self.evidence,
            "thresholds": self.thresholds,
            "current_value": self.current_value,
            "threshold_value": self.threshold_value,
            "security_id": self.security_id,
            "security_type": self.security_type,
            "tier": self.tier,
        }


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class EquitySignalDetector:
    """Detects equity review signals from portfolio + research metrics."""

    def __init__(self, thresholds: dict):
        self.t = thresholds

    def check_concentration(self, holding: dict) -> list[Signal]:
        cfg = self.t["concentration"]
        out: list[Signal] = []
        w = _num(holding.get("weight_pct"))
        limit = cfg["portfolio_pct_threshold"]
        if w is not None and w >= limit:
            sev = CRITICAL if w >= limit * 1.5 else WARN
            out.append(Signal(
                S_CONCENTRATION, sev, 0.9,
                f"Position is {w:.1f}% of portfolio (threshold {limit:.0f}%).",
                evidence={"weight_pct": w},
                thresholds={"portfolio_pct_threshold": limit},
                current_value=w, threshold_value=limit,
                security_id=holding.get("security_id"),
            ))
        target = _num(holding.get("target_weight_pct"))
        if w is not None and target and target > 0:
            dev = (w - target) / target * 100
            if dev >= cfg["target_deviation_pct"]:
                out.append(Signal(
                    S_CONCENTRATION, WARN, 0.75,
                    f"Position is {dev:.0f}% above target weight ({target:.1f}%).",
                    evidence={"weight_pct": w, "target_weight_pct": target},
                    thresholds={"target_deviation_pct": cfg["target_deviation_pct"]},
                    current_value=dev, threshold_value=cfg["target_deviation_pct"],
                    security_id=holding.get("security_id"),
                ))
        return out

    def check_valuation(self, research: dict, holding: dict | None = None) -> list[Signal]:
        cfg = self.t["valuation"]
        out: list[Signal] = []
        sid = (holding or {}).get("security_id")
        pctl = _num(research.get("forward_pe_percentile"))
        if pctl is not None and pctl >= cfg["forward_pe_percentile_threshold"]:
            out.append(Signal(
                S_VALUATION, WARN, 0.7,
                f"Forward P/E at {pctl:.0f}th percentile of its history.",
                evidence={"forward_pe_percentile": pctl, "forward_pe": research.get("forward_pe")},
                thresholds={"forward_pe_percentile_threshold": cfg["forward_pe_percentile_threshold"]},
                current_value=pctl, threshold_value=cfg["forward_pe_percentile_threshold"],
                security_id=sid, tier="analyst_consensus",
            ))
        peg = _num(research.get("peg"))
        if peg is not None and peg >= cfg["peg_threshold"]:
            out.append(Signal(
                S_VALUATION, WARN, 0.65,
                f"PEG ratio {peg:.2f} exceeds {cfg['peg_threshold']}.",
                evidence={"peg": peg},
                thresholds={"peg_threshold": cfg["peg_threshold"]},
                current_value=peg, threshold_value=cfg["peg_threshold"],
                security_id=sid, tier="analyst_consensus",
            ))
        return out

    def check_rapid_appreciation(self, holding: dict, research: dict) -> list[Signal]:
        cfg = self.t["rapid_appreciation"]
        gain = _num(holding.get("price_gain_6m"))
        if gain is None or gain < cfg["price_gain_threshold"]:
            return []
        upgrade = _num(research.get("eps_upgrade_6m"))
        if upgrade is not None and upgrade >= cfg["earnings_upgrade_threshold"]:
            return []
        up_txt = "no" if upgrade is None else f"{upgrade*100:.0f}%"
        return [Signal(
            S_RAPID_APPRECIATION, WARN, 0.7,
            f"Price rose {gain*100:.0f}% in {cfg['window_months']}m but EPS upgraded only {up_txt}.",
            evidence={"price_gain_6m": gain, "eps_upgrade_6m": upgrade},
            thresholds={"price_gain_threshold": cfg["price_gain_threshold"]},
            current_value=gain, threshold_value=cfg["price_gain_threshold"],
            security_id=holding.get("security_id"),
        )]

    def check_fundamental(self, research: dict, holding: dict | None = None) -> list[Signal]:
        cfg = self.t["fundamental"]
        sid = (holding or {}).get("security_id")
        out: list[Signal] = []
        checks = [
            ("revenue_growth_qoq", cfg["revenue_decline_qoq"], "Revenue declined QoQ"),
            ("eps_revision", cfg["eps_downgrade"], "EPS estimate cut"),
            ("margin_change", cfg["margin_decline"], "Operating margin compressed"),
            ("fcf_change", cfg["fcf_decline"], "Free cash flow declined"),
        ]
        for key, thr, label in checks:
            v = _num(research.get(key))
            if v is not None and v <= thr:
                sev = CRITICAL if v <= thr * 1.5 else WARN
                out.append(Signal(
                    S_FUNDAMENTAL, sev, 0.75,
                    f"{label}: {v*100:.1f}% (threshold {thr*100:.1f}%).",
                    evidence={key: v}, thresholds={key: thr},
                    current_value=v, threshold_value=thr,
                    security_id=sid, tier="analyst_consensus",
                ))
        return out

    def check_governance(self, research: dict, holding: dict | None = None) -> list[Signal]:
        cfg = self.t["governance"]
        sid = (holding or {}).get("security_id")
        out: list[Signal] = []
        pledge = _num(research.get("promoter_pledging"))
        if pledge is not None and pledge >= cfg["promoter_pledging_threshold"]:
            out.append(Signal(
                S_GOVERNANCE, CRITICAL, 0.9,
                f"Promoter pledging {pledge*100:.0f}% exceeds {cfg['promoter_pledging_threshold']*100:.0f}%.",
                evidence={"promoter_pledging": pledge},
                thresholds={"promoter_pledging_threshold": cfg["promoter_pledging_threshold"]},
                current_value=pledge, threshold_value=cfg["promoter_pledging_threshold"],
                security_id=sid, tier="fact",
            ))
        if cfg.get("auditor_concerns") and research.get("auditor_concerns"):
            out.append(Signal(
                S_GOVERNANCE, CRITICAL, 0.9, "Auditor concerns / qualification flagged.",
                evidence={"auditor_concerns": True}, thresholds={"auditor_concerns": True},
                security_id=sid, tier="fact",
            ))
        rpt = _num(research.get("related_party_cr"))
        if rpt is not None and rpt >= cfg["related_party_threshold_cr"]:
            out.append(Signal(
                S_GOVERNANCE, WARN, 0.7,
                f"Material related-party transactions Rs {rpt:.0f} Cr.",
                evidence={"related_party_cr": rpt},
                thresholds={"related_party_threshold_cr": cfg["related_party_threshold_cr"]},
                current_value=rpt, threshold_value=cfg["related_party_threshold_cr"],
                security_id=sid, tier="fact",
            ))
        if research.get("red_flags"):
            out.append(Signal(
                S_GOVERNANCE, WARN, 0.7, "Material red flags reported in disclosures.",
                evidence={"red_flags": research.get("red_flags")}, thresholds={},
                security_id=sid, tier="fact",
            ))
        return out

    def check_forward_returns(self, base_case_cagr: float | None, holding: dict | None = None) -> list[Signal]:
        cfg = self.t["forward_returns"]
        v = _num(base_case_cagr)
        if v is None or v >= cfg["expected_return_threshold"]:
            return []
        return [Signal(
            S_FORWARD_RETURNS, WARN, 0.6,
            f"Expected {cfg['horizon_years']}y CAGR {v*100:.1f}% below {cfg['expected_return_threshold']*100:.0f}% threshold.",
            evidence={"base_case_cagr": v},
            thresholds={"expected_return_threshold": cfg["expected_return_threshold"]},
            current_value=v, threshold_value=cfg["expected_return_threshold"],
            security_id=(holding or {}).get("security_id"), tier="ai_forecast",
        )]

    def check_all(self, holding: dict, research: dict, base_case_cagr: float | None = None) -> list[Signal]:
        """Run every equity detector and return the combined, id-tagged signal list."""
        sid = holding.get("security_id")
        signals: list[Signal] = []
        signals += self.check_concentration(holding)
        signals += self.check_valuation(research, holding)
        signals += self.check_rapid_appreciation(holding, research)
        signals += self.check_fundamental(research, holding)
        signals += self.check_governance(research, holding)
        signals += self.check_forward_returns(base_case_cagr, holding)
        for s in signals:
            if s.security_id is None:
                s.security_id = sid
        return signals


class MFSignalDetector:
    """Detects mutual-fund review signals from analytics + overlap + metadata."""

    def __init__(self, thresholds: dict):
        self.t = thresholds

    def check_underperformance(self, analytics: dict, scheme_code: str) -> list[Signal]:
        cfg = self.t["underperformance"]
        out: list[Signal] = []
        excess = _num(analytics.get("excess_3y"))
        if excess is not None and excess <= cfg["benchmark_gap_3y"]:
            out.append(Signal(
                S_UNDERPERFORMANCE, WARN, 0.75,
                f"3y return lagged benchmark by {abs(excess)*100:.1f}%.",
                evidence={"excess_3y": excess},
                thresholds={"benchmark_gap_3y": cfg["benchmark_gap_3y"]},
                current_value=excess, threshold_value=cfg["benchmark_gap_3y"],
                security_id=scheme_code, security_type="mf",
            ))
        return out

    def check_risk_adjusted(self, analytics: dict, scheme_code: str) -> list[Signal]:
        cfg = self.t["risk_adjusted"]
        out: list[Signal] = []
        sharpe = _num(analytics.get("sharpe_ratio"))
        if sharpe is not None and sharpe <= cfg["sharpe_threshold"]:
            out.append(Signal(
                S_RISK_ADJUSTED, WARN, 0.65,
                f"Sharpe ratio {sharpe:.2f} at/below {cfg['sharpe_threshold']}.",
                evidence={"sharpe_ratio": sharpe},
                thresholds={"sharpe_threshold": cfg["sharpe_threshold"]},
                current_value=sharpe, threshold_value=cfg["sharpe_threshold"],
                security_id=scheme_code, security_type="mf",
            ))
        cons = _num(analytics.get("performance_consistency_1y"))
        if cons is not None and cons < cfg["consistency_threshold"]:
            out.append(Signal(
                S_RISK_ADJUSTED, WARN, 0.6,
                f"Beat benchmark in only {cons*100:.0f}% of rolling 1y windows.",
                evidence={"performance_consistency_1y": cons},
                thresholds={"consistency_threshold": cfg["consistency_threshold"]},
                current_value=cons, threshold_value=cfg["consistency_threshold"],
                security_id=scheme_code, security_type="mf",
            ))
        return out

    def check_overlap(self, weight_overlap: float | None, jaccard: float | None, scheme_code: str) -> list[Signal]:
        cfg = self.t["overlap"]
        w = _num(weight_overlap)
        j = _num(jaccard)
        if (w is not None and w >= cfg["weight_overlap_threshold"]) or (j is not None and j >= cfg["jaccard_threshold"]):
            return [Signal(
                S_OVERLAP, WARN, 0.7,
                f"High overlap with another fund (weight {w or 0:.0f}%, Jaccard {j or 0:.2f}).",
                evidence={"weight_overlap_pct": w, "jaccard": j},
                thresholds={"weight_overlap_threshold": cfg["weight_overlap_threshold"], "jaccard_threshold": cfg["jaccard_threshold"]},
                current_value=w, threshold_value=cfg["weight_overlap_threshold"],
                security_id=scheme_code, security_type="mf",
            )]
        return []


    def check_expenses(self, expense_ratio: float | None, scheme_code: str) -> list[Signal]:
        cfg = self.t["expenses"]
        er = _num(expense_ratio)
        if er is None or er < cfg["expense_ratio_threshold"]:
            return []
        return [Signal(
            S_EXPENSES, WARN, 0.6,
            f"Expense ratio {er:.2f}% exceeds {cfg['expense_ratio_threshold']}%.",
            evidence={"expense_ratio": er},
            thresholds={"expense_ratio_threshold": cfg["expense_ratio_threshold"]},
            current_value=er, threshold_value=cfg["expense_ratio_threshold"],
            security_id=scheme_code, security_type="mf",
        )]

    def check_manager(self, manager_changes: int | None, scheme_code: str) -> list[Signal]:
        cfg = self.t["manager"]
        mc = _num(manager_changes)
        if mc is None or mc < cfg["manager_changes_threshold"]:
            return []
        return [Signal(
            S_MANAGER, WARN, 0.65,
            f"{int(mc)} fund-manager/process change(s) — review continuity.",
            evidence={"manager_changes": mc},
            thresholds={"manager_changes_threshold": cfg["manager_changes_threshold"]},
            current_value=mc, threshold_value=cfg["manager_changes_threshold"],
            security_id=scheme_code, security_type="mf",
        )]

    def check_concentration(self, top5_pct: float | None, single_pct: float | None, scheme_code: str) -> list[Signal]:
        cfg = self.t["concentration"]
        out: list[Signal] = []
        t5 = _num(top5_pct)
        if t5 is not None and t5 >= cfg["top5_threshold"]:
            out.append(Signal(
                S_MF_CONCENTRATION, WARN, 0.6,
                f"Top-5 holdings are {t5:.0f}% of scheme (threshold {cfg['top5_threshold']:.0f}%).",
                evidence={"top5_pct": t5},
                thresholds={"top5_threshold": cfg["top5_threshold"]},
                current_value=t5, threshold_value=cfg["top5_threshold"],
                security_id=scheme_code, security_type="mf",
            ))
        sp = _num(single_pct)
        if sp is not None and sp >= cfg["single_stock_threshold"]:
            out.append(Signal(
                S_MF_CONCENTRATION, WARN, 0.65,
                f"Single stock {sp:.0f}% of scheme (threshold {cfg['single_stock_threshold']:.0f}%).",
                evidence={"single_stock_pct": sp},
                thresholds={"single_stock_threshold": cfg["single_stock_threshold"]},
                current_value=sp, threshold_value=cfg["single_stock_threshold"],
                security_id=scheme_code, security_type="mf",
            ))
        return out

    def check_all(self, scheme_metrics: dict, overlap_metrics: dict | None = None) -> list[Signal]:
        """Run every MF detector. ``scheme_metrics`` bundles analytics + metadata + concentration."""
        code = scheme_metrics.get("scheme_code") or scheme_metrics.get("scheme_name") or ""
        analytics = scheme_metrics.get("analytics", scheme_metrics)
        om = overlap_metrics or {}
        signals: list[Signal] = []
        signals += self.check_underperformance(analytics, code)
        signals += self.check_risk_adjusted(analytics, code)
        signals += self.check_overlap(om.get("weight_overlap_pct"), om.get("jaccard"), code)
        signals += self.check_expenses(scheme_metrics.get("expense_ratio"), code)
        signals += self.check_manager(scheme_metrics.get("manager_changes"), code)
        signals += self.check_concentration(scheme_metrics.get("top5_pct"), scheme_metrics.get("single_stock_pct"), code)
        return signals

