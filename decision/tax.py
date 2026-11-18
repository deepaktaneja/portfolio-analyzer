"""Tax estimation for equity sell/trim *reviews* (Stage 8).

These are transparent ESTIMATES for advisory review only — not tax advice. Rates
are configurable and clearly labelled; users must verify with current rules. No
trade is executed. Applies to Indian listed equity delivery transactions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Configurable defaults (post-July-2024 listed-equity delivery assumptions).
STCG_RATE = 0.20
LTCG_RATE = 0.125
LTCG_EXEMPTION = 125_000.0
STT_RATE = 0.001  # on sell value
LONG_TERM_DAYS = 365


@dataclass
class TaxImpact:
    holding_days: int | None
    is_long_term: bool | None
    estimated_gain: float | None
    estimated_tax: float | None
    stt: float | None
    net_proceeds: float | None
    note: str


def _to_date(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def estimate_tax_impact(
    buy_date, sell_date, quantity, buy_price, sell_price,
    stcg_rate: float = STCG_RATE, ltcg_rate: float = LTCG_RATE,
    ltcg_exemption: float = LTCG_EXEMPTION, stt_rate: float = STT_RATE,
) -> TaxImpact:
    """Estimate capital-gains tax + STT for a sell/trim review scenario."""
    bd, sd = _to_date(buy_date), _to_date(sell_date)
    try:
        q = float(quantity)
        bp = float(buy_price)
        sp = float(sell_price)
    except (TypeError, ValueError):
        return TaxImpact(None, None, None, None, None, None, "Insufficient price/quantity data.")

    if bd is None or sd is None or q <= 0 or bp <= 0 or sp <= 0:
        return TaxImpact(None, None, None, None, None, None, "Insufficient price/quantity/date data.")

    holding_days = (sd - bd).days
    is_long_term = holding_days >= LONG_TERM_DAYS
    gain = (sp - bp) * q
    sell_value = sp * q
    stt = sell_value * stt_rate

    if gain <= 0:
        tax = 0.0
        note = "No capital gain (loss) — no capital-gains tax estimated; STT still applies on sell."
    elif is_long_term:
        taxable = max(0.0, gain - ltcg_exemption)
        tax = taxable * ltcg_rate
        note = (f"Long-term ({holding_days}d): ~{ltcg_rate*100:.1f}% on gains above "
                f"Rs {ltcg_exemption:,.0f} exemption. Estimate only.")
    else:
        tax = gain * stcg_rate
        note = f"Short-term ({holding_days}d): ~{stcg_rate*100:.1f}% on gains. Estimate only."

    return TaxImpact(
        holding_days=holding_days,
        is_long_term=is_long_term,
        estimated_gain=round(gain, 2),
        estimated_tax=round(tax, 2),
        stt=round(stt, 2),
        net_proceeds=round(sell_value - tax - stt, 2),
        note=note,
    )
