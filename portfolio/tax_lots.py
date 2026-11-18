"""FIFO tax-lot reconstruction + lot-level tax estimation (Stage 9).

Standalone (does not modify accounting/ledger). Produces remaining open lots with
per-unit cost basis and buy dates, then estimates tax by consuming lots FIFO.
"""
from __future__ import annotations

from datetime import date

from portfolio.tax import estimate_capital_gains, get_tax_rules

BUY_SIDES = {"BUY", "PURCHASE", "SIP", "OPENING", "OPEN", "OPENING_POSITION", "LUMP_SUM", "SUBSCRIPTION"}
SELL_SIDES = {"SELL", "REDEMPTION", "SWITCH_OUT", "SWP", "WITHDRAW"}


def _to_date(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _side(t: dict) -> str:
    return str(t.get("side") or t.get("buy_sell") or "").upper()


def _qty(t: dict) -> float:
    for k in ("units", "quantity", "qty"):
        try:
            v = float(t.get(k) or 0)
        except (TypeError, ValueError):
            continue
        if v:
            return v
    return 0.0


def _price(t: dict) -> float | None:
    for k in ("price", "nav", "avg_price"):
        v = t.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    # derive from amount / quantity
    try:
        q = _qty(t)
        amt = float(t.get("amount") or t.get("net_amount") or 0)
        if q > 0 and amt:
            return abs(amt) / q
    except (TypeError, ValueError):
        pass
    return None


def _security(t: dict) -> str:
    return str(t.get("isin") or t.get("security") or t.get("scheme_code") or "").strip().upper()


def build_fifo_lots(transactions: list[dict], security_id: str, as_of=None) -> list[dict]:
    """Return remaining open FIFO lots for ``security_id`` as of ``as_of`` (optional)."""
    sid = str(security_id).strip().upper()
    cut = _to_date(as_of)
    events = []
    for t in transactions or []:
        if _security(t) != sid:
            continue
        d = _to_date(t.get("trade_date") or t.get("date") or t.get("settlement_date"))
        if d is None or (cut is not None and d > cut):
            continue
        events.append((d, _side(t), _qty(t), _price(t)))
    events.sort(key=lambda e: e[0])

    lots: list[dict] = []
    for d, side, q, p in events:
        if q <= 0:
            continue
        if side in BUY_SIDES:
            lots.append({"buy_date": d, "quantity": q, "cost_per_unit": p})
        elif side in SELL_SIDES:
            remaining = q
            while remaining > 1e-9 and lots:
                lot = lots[0]
                take = min(lot["quantity"], remaining)
                lot["quantity"] -= take
                remaining -= take
                if lot["quantity"] <= 1e-9:
                    lots.pop(0)
    return [lot for lot in lots if lot["quantity"] > 1e-9]


def estimate_lot_tax(lots: list[dict], sell_date, sell_price: float, quantity: float,
                     *, rules: dict | None = None, is_mf: bool = False,
                     exit_load_pct: float = 0.0) -> dict:
    """Consume FIFO lots to estimate total tax for selling ``quantity`` units."""
    rules = rules or get_tax_rules(sell_date)
    remaining = float(quantity)
    total_tax = 0.0
    total_gain = 0.0
    details = []
    missing = False
    for lot in lots:
        if remaining <= 1e-9:
            break
        take = min(lot["quantity"], remaining)
        outcome = estimate_capital_gains(
            lot["buy_date"], sell_date, take, lot.get("cost_per_unit"), sell_price,
            rules=rules, is_mf=is_mf, exit_load_pct=exit_load_pct,
        )
        details.append({
            "buy_date": lot["buy_date"].isoformat(),
            "quantity": round(take, 4),
            "cost_per_unit": lot.get("cost_per_unit"),
            "gain": outcome.gain,
            "tax": outcome.capital_gains_tax,
            "is_long_term": outcome.is_long_term,
            "note": outcome.note,
        })
        if outcome.missing_cost_basis:
            missing = True
        else:
            total_tax += outcome.capital_gains_tax or 0.0
            total_gain += outcome.gain or 0.0
        remaining -= take
    return {
        "requested_qty": round(float(quantity), 4),
        "covered_qty": round(float(quantity) - max(0.0, remaining), 4),
        "uncovered_qty": round(max(0.0, remaining), 4),
        "estimated_tax": round(total_tax, 2),
        "estimated_gain": round(total_gain, 2),
        "missing_cost_basis": missing,
        "lots": details,
    }
