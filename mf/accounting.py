"""MF accounting & XIRR engine (Stage 3/4)."""
from __future__ import annotations
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from models.mf import MFTransaction
from src.xirr import compute_xirr, annualize_pct


def build_folio_units(transactions: list) -> dict[str, Decimal]:
    """Reconstruct unit holdings from MF transactions (FIFO).

    Accepts both MFTransaction objects and dict-like transactions.
    """
    holdings: dict[str, Decimal] = {}
    for t in transactions:
        if hasattr(t, "side"):
            side = str(t.side).upper()
            units = Decimal(str(t.units or 0))
            sid = t.scheme_code
        else:
            side = str(t.get("side") or t.get("side_type") or "").upper()
            units = Decimal(str(t.get("units") or t.get("quantity") or 0))
            sid = t.get("isin") or t.get("security") or t.get("scheme_code")
        if side in ("SIP", "PURCHASE", "BUY", "LUMP_SUM", "SUBSCRIPTION", "allotment", "bonus", "growth", "accumulation", "reinvest"):
            holdings[sid] = holdings.get(sid, Decimal("0")) + units
        elif side in ("REDEMPTION", "SWITCH_OUT", "SWP", "WITHDRAW", "SELL"):
            holdings[sid] = max(Decimal("0"), holdings.get(sid, Decimal("0")) - units)
        elif side == "SWITCH_IN":
            pass
        elif side == "IDCW":
            pass
    return holdings


def build_fund_cashflows(transactions: list[MFTransaction], end_date: str, nav_at_end: Decimal) -> list[tuple]:
    """Build cash-flow list for MF XIRR."""
    flows: list[tuple] = []
    for t in transactions:
        d = t.trade_date
        if not d:
            continue
        amt = -(t.amount or Decimal("0"))
        side = t.side
        if side in ("SIP", "PURCHASE", "LUMP_SUM", "SUBSCRIPTION", "allotment", "bonus", "growth", "accumulation", "reinvest"):
            label = "buy"
            flows.append((d, amt, label))
        elif side in ("REDEMPTION", "SWITCH_OUT", "SWP", "WITHDRAW"):
            label = "sell"
            flows.append((d, -amt, label))
        elif side == "IDCW":
            label = "dividend"
            flows.append((d, -((t.amount or Decimal("0"))), label))
    holdings = build_folio_units(transactions)
    for sid, units in holdings.items():
        if units > 0:
            flows.append((end_date, units * nav_at_end, "terminal_value"))
    return flows


def calculate_fund_xirr(transactions: list[MFTransaction], end_date: str, nav_at_end: Optional[Decimal] = None) -> dict:
    """Calculate scheme-level XIRR."""
    if not transactions:
        return {"xirr": None, "annualized": None, "cashflows": [], "reason": "no_transactions"}
    nav_val = nav_at_end or Decimal("0")
    flows = build_fund_cashflows(transactions, end_date, nav_val)
    if len(flows) < 2:
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "insufficient_flows"}
    has_neg = any(a < 0 for _, a, _ in flows)
    has_pos = any(a > 0 for _, a, _ in flows)
    if not (has_neg and has_pos):
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "missing_opposite_sign"}
    try:
        xv = compute_xirr(flows)
    except Exception:
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "solver_failed"}
    if xv is None:
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "no_solution"}
    try:
        ann = annualize_pct(xv)
    except Exception:
        ann = None
    return {"xirr": float(xv), "annualized": ann, "cashflows": flows, "reason": None}


def calculate_portfolio_mf_xirr(portfolio_transactions: list[MFTransaction], end_date: str, nav_data_by_scheme: dict[str, Decimal]) -> dict:
    """Calculate overall MF portfolio XIRR."""
    all_flows: list[tuple] = []
    for t in portfolio_transactions:
        d = t.trade_date
        if not d:
            continue
        amt = -(t.amount or Decimal("0"))
        side = t.side
        if side in ("SIP", "PURCHASE", "LUMP_SUM"):
            all_flows.append((d, amt, "buy"))
        elif side in ("REDEMPTION", "SWP"):
            all_flows.append((d, -amt, "sell"))
        elif side == "IDCW":
            all_flows.append((d, -((t.amount or Decimal("0"))), "dividend"))
    holdings: dict[str, Decimal] = {}
    for t in portfolio_transactions:
        if t.side in ("SIP", "PURCHASE", "LUMP_SUM"):
            holdings[t.scheme_code] = holdings.get(t.scheme_code, Decimal("0")) + (t.units or Decimal("0"))
    for sid, units in holdings.items():
        nav = nav_data_by_scheme.get(sid, Decimal("0"))
        if units > 0 and nav > 0:
            all_flows.append((end_date, Decimal(units) * nav, "terminal_value"))
    if len(all_flows) < 2:
        return {"xirr": None, "annualized": None, "cashflows": all_flows, "reason": "insufficient_flows"}
    has_neg = any(a < 0 for _, a, _ in all_flows)
    has_pos = any(a > 0 for _, a, _ in all_flows)
    if not (has_neg and has_pos):
        return {"xirr": None, "annualized": None, "cashflows": all_flows, "reason": "missing_opposite_sign"}
    try:
        xv = compute_xirr(all_flows)
    except Exception:
        return {"xirr": None, "annualized": None, "cashflows": all_flows, "reason": "solver_failed"}
    if xv is None:
        return {"xirr": None, "annualized": None, "cashflows": all_flows, "reason": "no_solution"}
    try:
        ann = annualize_pct(xv)
    except Exception:
        ann = None
    return {"xirr": float(xv), "annualized": ann, "cashflows": all_flows, "reason": None}


def calculate_invested_amount(transactions: list[MFTransaction]) -> Decimal:
    """Total invested amount (purchases/SIPs)."""
    total = Decimal("0")
    for t in transactions:
        if t.side in ("SIP", "PURCHASE", "LUMP_SUM", "SUBSCRIPTION", "allotment", "bonus", "growth", "accumulation", "reinvest"):
            total += t.amount or Decimal("0")
    return total


def calculate_current_value(units: Decimal, nav: Decimal) -> Decimal:
    """Current value = units x NAV."""
    return units * nav


def calculate_realized_unrealized_gains(transactions: list, nav_data: dict[str, Decimal]) -> dict[str, dict]:
    """Calculate realized and unrealized gains per scheme.

    Accepts both MFTransaction objects and dict-like transactions.
    """
    holdings = build_folio_units(transactions)
    invested: dict[str, Decimal] = {}
    redeemed: dict[str, Decimal] = {}
    for t in transactions:
        if hasattr(t, "side"):
            side = str(t.side).upper()
            amt = Decimal(str(t.amount or t.net_amount or 0))
            sid = t.scheme_code
        else:
            side = str(t.get("side") or t.get("side_type") or "").upper()
            amt = Decimal(str(t.get("amount") or t.get("net_amount") or 0))
            sid = t.get("isin") or t.get("security") or t.get("scheme_code")
        if side in ("SIP", "PURCHASE", "BUY", "LUMP_SUM", "SUBSCRIPTION", "allotment", "bonus", "growth", "accumulation", "reinvest"):
            invested[sid] = invested.get(sid, Decimal("0")) + amt
        elif side in ("REDEMPTION", "SWITCH_OUT", "SWP", "WITHDRAW", "SELL"):
            redeemed[sid] = redeemed.get(sid, Decimal("0")) - amt
    result: dict[str, dict] = {}
    for sid, units in holdings.items():
        nav = nav_data.get(sid, Decimal("0"))
        current_val = units * nav if nav > 0 else Decimal("0")
        inv = invested.get(sid, Decimal("0"))
        red = redeemed.get(sid, Decimal("0"))
        realized = red - inv if inv > 0 else Decimal("0")
        unrealized = current_val - max(Decimal("0"), inv - red)
        result[sid] = {
            "units": float(units),
            "nav": float(nav) if nav else None,
            "current_value": float(current_val),
            "invested": float(inv),
            "redeemed": float(red),
            "realized_gain": float(realized),
            "unrealized_gain": float(unrealized),
            "missing_nav": nav <= 0,
        }
    return result
print("mf/accounting.py created")
