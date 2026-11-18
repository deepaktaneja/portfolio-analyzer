"""Forward-looking analysis: bear/base/bull valuation scenarios and expected returns.

All financial calculations are deterministic pure Python. The LLM never
computes these figures — it only interprets them.

Clearly distinguishes:
  - Historical facts: actual revenue, EPS, margins from MCP evidence
  - Analyst consensus: analyst EPS/revenue estimates from MCP
  - AI-generated forecasts: our own projections, clearly labelled
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def _d(value: Any) -> Decimal:
    """Coerce to Decimal, returning Decimal('0') on failure."""
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def _r2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _r4(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@dataclass
class GrowthAssumptions:
    """Transparent assumptions for forward projections (1-3 years)."""

    revenue_growth_y1: Decimal = Decimal("0.10")
    revenue_growth_y2: Decimal = Decimal("0.10")
    revenue_growth_y3: Decimal = Decimal("0.10")
    eps_growth_y1: Decimal = Decimal("0.10")
    eps_growth_y2: Decimal = Decimal("0.10")
    eps_growth_y3: Decimal = Decimal("0.10")
    operating_margin_y1: Decimal = Decimal("0.15")
    operating_margin_y2: Decimal = Decimal("0.15")
    operating_margin_y3: Decimal = Decimal("0.15")
    tax_rate: Decimal = Decimal("0.25")
    terminal_growth: Decimal = Decimal("0.03")
    discount_rate: Decimal = Decimal("0.12")
    pe_multiple_base: Decimal = Decimal(20)
    pe_multiple_bear: Decimal = Decimal(15)
    pe_multiple_bull: Decimal = Decimal(25)

    def to_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.__dict__.items() if not k.startswith("_")}


@dataclass
class ScenarioProjection:
    """One scenario's 1-3 year projection."""
    scenario: str
    year: int
    revenue: Decimal
    eps: Decimal
    operating_margin: Decimal
    net_income: Decimal
    free_cash_flow: Decimal


@dataclass
class ValuationScenario:
    """Complete valuation scenario with expected return."""
    scenario: str
    pe_multiple: Decimal
    projected_eps_y3: Decimal
    estimated_price_y3: Decimal
    current_price: Decimal
    expected_return_3y_pct: Decimal
    expected_return_cagr_pct: Decimal
    projections: list[ScenarioProjection] = field(default_factory=list)


@dataclass
class ForwardAnalysis:
    """Complete forward-looking analysis result."""
    symbol: str
    current_price: Decimal
    current_revenue: Decimal | None
    current_eps: Decimal | None
    current_margin: Decimal | None
    analyst_consensus_eps_y1: Decimal | None
    analyst_consensus_eps_y2: Decimal | None
    assumptions: GrowthAssumptions
    scenarios: list[ValuationScenario] = field(default_factory=list)
    base_case_cagr: Decimal | None = None
    data_tier_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "current_price": float(self.current_price) if self.current_price else None,
            "current_revenue": float(self.current_revenue) if self.current_revenue else None,
            "current_eps": float(self.current_eps) if self.current_eps else None,
            "current_margin": float(self.current_margin) if self.current_margin else None,
            "analyst_consensus_eps_y1": float(self.analyst_consensus_eps_y1) if self.analyst_consensus_eps_y1 else None,
            "analyst_consensus_eps_y2": float(self.analyst_consensus_eps_y2) if self.analyst_consensus_eps_y2 else None,
            "assumptions": self.assumptions.to_dict(),
            "base_case_cagr_3y": float(self.base_case_cagr) if self.base_case_cagr else None,
            "scenarios": [
                {
                    "scenario": s.scenario,
                    "pe_multiple": float(s.pe_multiple),
                    "projected_eps_y3": float(s.projected_eps_y3),
                    "estimated_price_y3": float(s.estimated_price_y3),
                    "current_price": float(s.current_price),
                    "expected_return_3y_pct": float(s.expected_return_3y_pct),
                    "expected_return_cagr_pct": float(s.expected_return_cagr_pct),
                    "projections": [
                        {
                            "year": p.year,
                            "revenue": float(p.revenue),
                            "eps": float(p.eps),
                            "operating_margin": float(p.operating_margin),
                            "net_income": float(p.net_income),
                            "free_cash_flow": float(p.free_cash_flow),
                        }
                        for p in s.projections
                    ],
                }
                for s in self.scenarios
            ],
            "data_tier_notes": self.data_tier_notes,
        }


class ForecastEngine:
    """Deterministic forward-looking analysis engine."""
    def __init__(self, assumptions: GrowthAssumptions | None = None):
        self.assumptions = assumptions or GrowthAssumptions()

    def analyze(
        self,
        symbol: str,
        current_price: Any,
        historical: dict[str, Any] | None = None,
        analyst_consensus: dict[str, Any] | None = None,
    ) -> ForwardAnalysis:
        """Build bear/base/bull valuation scenarios with transparent assumptions."""
        price = _d(current_price)
        hist = historical or {}
        analyst = analyst_consensus or {}

        current_revenue = _d(hist.get("revenue")) if hist.get("revenue") is not None else None
        current_eps = _d(hist.get("eps")) if hist.get("eps") is not None else None
        current_margin = _d(hist.get("operating_margin")) if hist.get("operating_margin") is not None else None

        a_eps_y1 = _d(analyst.get("eps_estimate_y1")) if analyst.get("eps_estimate_y1") is not None else None
        a_eps_y2 = _d(analyst.get("eps_estimate_y2")) if analyst.get("eps_estimate_y2") is not None else None

        notes: list[str] = []
        if current_revenue is None:
            notes.append("No historical revenue from MCP — using AI forecast only")
        if current_eps is None:
            notes.append("No historical EPS from MCP — using AI forecast only")
        if current_margin is None:
            notes.append("No historical margin from MCP — using AI forecast only")
        if a_eps_y1 is None:
            notes.append("No analyst consensus EPS Y1 — AI forecast is the only estimate")
        else:
            notes.append(f"Analyst consensus EPS Y1: {a_eps_y1} (from MCP analyst tool)")

        analysis = ForwardAnalysis(
            symbol=symbol,
            current_price=price,
            current_revenue=current_revenue,
            current_eps=current_eps,
            current_margin=current_margin,
            analyst_consensus_eps_y1=a_eps_y1,
            analyst_consensus_eps_y2=a_eps_y2,
            assumptions=self.assumptions,
            data_tier_notes=notes,
        )

        for scenario_name in ("bear", "base", "bull"):
            scenario = self._build_scenario(
                scenario_name=scenario_name,
                current_eps=current_eps,
                current_revenue=current_revenue,
                current_margin=current_margin,
                current_price=price,
                a_eps_y1=a_eps_y1,
                a_eps_y2=a_eps_y2,
            )
            analysis.scenarios.append(scenario)

        base = next((s for s in analysis.scenarios if s.scenario == "base"), None)
        if base is not None and price > 0:
            analysis.base_case_cagr = _r4(base.expected_return_cagr_pct / Decimal(100))

        return analysis

    def _build_scenario(
        self,
        scenario_name: str,
        current_eps: Decimal | None,
        current_revenue: Decimal | None,
        current_margin: Decimal | None,
        current_price: Decimal,
        a_eps_y1: Decimal | None,
        a_eps_y2: Decimal | None,
    ) -> ValuationScenario:
        """Build one bear/base/bull scenario with 1-3 year projections."""
        a = self.assumptions

        if scenario_name == "bear":
            rev_g = [a.revenue_growth_y1 * Decimal("0.5"), a.revenue_growth_y2 * Decimal("0.5"), a.revenue_growth_y3 * Decimal("0.5")]
            eps_g = [a.eps_growth_y1 * Decimal("0.5"), a.eps_growth_y2 * Decimal("0.5"), a.eps_growth_y3 * Decimal("0.5")]
            marg = [a.operating_margin_y1 * Decimal("0.85"), a.operating_margin_y2 * Decimal("0.85"), a.operating_margin_y3 * Decimal("0.85")]
            pe = a.pe_multiple_bear
        elif scenario_name == "bull":
            rev_g = [a.revenue_growth_y1 * Decimal("1.5"), a.revenue_growth_y2 * Decimal("1.5"), a.revenue_growth_y3 * Decimal("1.5")]
            eps_g = [a.eps_growth_y1 * Decimal("1.5"), a.eps_growth_y2 * Decimal("1.5"), a.eps_growth_y3 * Decimal("1.5")]
            marg = [a.operating_margin_y1 * Decimal("1.10"), a.operating_margin_y2 * Decimal("1.10"), a.operating_margin_y3 * Decimal("1.10")]
            pe = a.pe_multiple_bull
        else:
            rev_g = [a.revenue_growth_y1, a.revenue_growth_y2, a.revenue_growth_y3]
            eps_g = [a.eps_growth_y1, a.eps_growth_y2, a.eps_growth_y3]
            marg = [a.operating_margin_y1, a.operating_margin_y2, a.operating_margin_y3]
            pe = a.pe_multiple_base

        base_eps_y1 = a_eps_y1 if a_eps_y1 is not None else (current_eps or Decimal(0)) * (1 + eps_g[0])
        base_eps_y2 = a_eps_y2 if a_eps_y2 is not None else base_eps_y1 * (1 + eps_g[1])
        base_eps_y3 = base_eps_y2 * (1 + eps_g[2])

        projections: list[ScenarioProjection] = []
        rev = current_revenue or Decimal(0)
        eps = current_eps or Decimal(0)
        for i, (rg, eg, mg) in enumerate(zip(rev_g, eps_g, marg), start=1):
            rev = rev * (1 + rg) if rev > 0 else base_eps_y1 * Decimal(10)
            eps = (base_eps_y1 if i == 1 else eps) * (1 + eg) if i > 1 else base_eps_y1
            margin = mg
            net_income = rev * margin * (1 - a.tax_rate) if rev > 0 else eps * Decimal(100)
            fcf = net_income * Decimal("0.8")
            projections.append(
                ScenarioProjection(
                    scenario=scenario_name,
                    year=i,
                    revenue=_r2(rev),
                    eps=_r2(eps),
                    operating_margin=_r4(margin),
                    net_income=_r2(net_income),
                    free_cash_flow=_r2(fcf),
                )
            )

        projected_eps_y3 = projections[-1].eps if projections else base_eps_y3
        estimated_price_y3 = projected_eps_y3 * pe

        expected_return_3y = Decimal(0)
        expected_return_cagr = Decimal(0)
        if current_price > 0:
            expected_return_3y = _r2((estimated_price_y3 - current_price) / current_price * Decimal(100))
            if estimated_price_y3 > 0 and current_price > 0:
                ratio = estimated_price_y3 / current_price
                cagr = (ratio ** (Decimal(1) / Decimal(3)) - Decimal(1)) * Decimal(100)
                expected_return_cagr = _r2(cagr)

        return ValuationScenario(
            scenario=scenario_name,
            pe_multiple=pe,
            projected_eps_y3=_r2(projected_eps_y3),
            estimated_price_y3=_r2(estimated_price_y3),
            current_price=current_price,
            expected_return_3y_pct=expected_return_3y,
            expected_return_cagr_pct=expected_return_cagr,
            projections=projections,
        )