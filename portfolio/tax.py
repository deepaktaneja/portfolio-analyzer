"""Date-effective Indian tax rules + capital-gains estimation (Stage 9).

Estimates only — not tax advice. Rules are selected by exit date with a
configurable effective-date change (23-Jul-2024). Missing cost basis is flagged,
never assumed. Applies to listed equity / equity-oriented funds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Effective-date rule sets for listed equity & equity-oriented MF (delivery).
_RULE_CHANGE_DATE = date(2024, 7, 23)
_RULES_BEFORE = {"stcg_rate": 0.15, "ltcg_rate": 0.10, "ltcg_exemption": 100_000.0}
_RULES_AFTER = {"stcg_rate": 0.20, "ltcg_rate": 0.125, "ltcg_exemption": 125_000.0}


def _to_date(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def get_tax_rules(as_of_date=None, overrides: dict | None = None) -> dict:
    """Return the applicable tax-rule set for the given exit date (+ config overrides)."""
    d = _to_date(as_of_date) or date.today()
    base = dict(_RULES_AFTER if d >= _RULE_CHANGE_DATE else _RULES_BEFORE)
    base.update({"stt_rate": 0.001, "ltcg_holding_days": 365, "exit_load_default_pct": 0.0})
    ov = overrides or {}
    for key in ("stcg_rate", "ltcg_rate", "ltcg_exemption"):
        if ov.get("equity_" + key) is not None:
            base[key] = float(ov["equity_" + key])
    if ov.get("stt_rate") is not None:
        base["stt_rate"] = float(ov["stt_rate"])
    if ov.get("ltcg_holding_days") is not None:
        base["ltcg_holding_days"] = int(ov["ltcg_holding_days"])
    if ov.get("exit_load_default_pct") is not None:
        base["exit_load_default_pct"] = float(ov["exit_load_default_pct"])
    return base


@dataclass
class TaxOutcome:
    holding_days: int | None
    is_long_term: bool | None
    gain: float | None
    capital_gains_tax: float | None
    stt: float | None
    exit_load: float
    transaction_cost: float
    net_proceeds: float | None
    total_cost: float | None
    missing_cost_basis: bool
    note: str


def estimate_capital_gains(
    buy_date, sell_date, quantity, buy_price, sell_price,
    *, rules: dict | None = None, is_mf: bool = False, exit_load_pct: float = 0.0,
    transaction_cost_pct: float = 0.0005,
) -> TaxOutcome:
    """Estimate capital-gains tax + STT + exit load for a sell of ``quantity`` units."""
    rules = rules or get_tax_rules(sell_date)
    bd, sd = _to_date(buy_date), _to_date(sell_date)
    try:
        q = float(quantity)
        bp = float(buy_price) if buy_price is not None else None
        sp = float(sell_price)
    except (TypeError, ValueError):
        return TaxOutcome(None, None, None, None, None, 0.0, 0.0, None, None, True,
                          "Insufficient quantity/price data — cannot estimate.")
    if bp is None or bp <= 0:
        return TaxOutcome(None, None, None, None, None, 0.0, 0.0, None, None, True,
                          "Missing cost basis — tax not estimated (cost basis unknown).")
    if bd is None or sd is None or q <= 0 or sp <= 0:
        return TaxOutcome(None, None, None, None, None, 0.0, 0.0, None, None, True,
                          "Insufficient date/quantity data — cannot estimate.")

    holding_days = (sd - bd).days
    is_long_term = holding_days >= rules["ltcg_holding_days"]
    gain = (sp - bp) * q
    sell_value = sp * q
    stt = sell_value * rules["stt_rate"]
    txn = sell_value * transaction_cost_pct
    exit_load = sell_value * exit_load_pct if is_mf else 0.0

    if gain <= 0:
        cg_tax = 0.0
        note = "No capital gain — no capital-gains tax; STT applies on sell."
    elif is_long_term:
        taxable = max(0.0, gain - rules["ltcg_exemption"])
        cg_tax = taxable * rules["ltcg_rate"]
        note = (f"Long-term ({holding_days}d): ~{rules['ltcg_rate']*100:.1f}% above "
                f"Rs {rules['ltcg_exemption']:,.0f} exemption (estimate).")
    else:
        cg_tax = gain * rules["stcg_rate"]
        note = f"Short-term ({holding_days}d): ~{rules['stcg_rate']*100:.1f}% (estimate)."

    total_cost = cg_tax + stt + txn + exit_load
    return TaxOutcome(
        holding_days=holding_days,
        is_long_term=is_long_term,
        gain=round(gain, 2),
        capital_gains_tax=round(cg_tax, 2),
        stt=round(stt, 2),
        exit_load=round(exit_load, 2),
        transaction_cost=round(txn, 2),
        net_proceeds=round(sell_value - total_cost, 2),
        total_cost=round(total_cost, 2),
        missing_cost_basis=False,
        note=note,
    )
