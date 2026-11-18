"""MF accounting & XIRR engine (Stage 3/4)."""
from __future__ import annotations

from decimal import Decimal

from models.mf import MFTransaction
from src.xirr import annualize_pct, compute_xirr

# --- Stage 11 audit: normalised side classification & field accessors -------
# Compared against UPPER-cased sides so lower/upper source values behave identically.
BUY_SIDES = frozenset({
    "SIP", "PURCHASE", "BUY", "LUMP_SUM", "SUBSCRIPTION", "ALLOTMENT",
    "BONUS", "GROWTH", "ACCUMULATION", "REINVEST", "SWITCH_IN", "STP_IN",
})
SELL_SIDES = frozenset({
    "REDEMPTION", "SWITCH_OUT", "SWP", "WITHDRAW", "SELL", "STP_OUT",
})
DIVIDEND_SIDES = frozenset({"IDCW", "DIVIDEND", "DIVIDEND_PAYOUT", "PAYOUT"})


def _norm_side(v) -> str:
    return str(v or "").strip().upper().replace(" ", "_").replace("-", "_")


def _field(t, *names, default=None):
    if isinstance(t, dict):
        for n in names:
            if t.get(n) is not None:
                return t.get(n)
        return default
    for n in names:
        v = getattr(t, n, None)
        if v is not None:
            return v
    return default


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v)) if v not in (None, "") else Decimal(0)
    except (TypeError, ValueError, ArithmeticError):
        return Decimal(0)


def _units_r(t) -> Decimal:
    return _dec(_field(t, "units", "quantity", "qty"))


def _amount_r(t) -> Decimal:
    return _dec(_field(t, "amount", "net_amount"))


def _sid_r(t) -> str:
    return str(_field(t, "scheme_code", "isin", "security") or "")


def _date_r(t):
    return _field(t, "trade_date", "date", "transaction_date")


def _side_r(t) -> str:
    return _norm_side(_field(t, "side", "side_type"))


def build_folio_units(transactions: list) -> dict[str, Decimal]:
    """Reconstruct unit holdings from MF transactions.

    Handles SIPs/lump-sum purchases, redemptions, **switches (in & out)**, SWP/STP,
    bonus/reinvestment allotments and IDCW (no unit change). Accepts MFTransaction
    objects or dict-like transactions. (Stage 11 audit fix.)
    """
    holdings: dict[str, Decimal] = {}
    for t in transactions or []:
        sid = _sid_r(t)
        if not sid:
            continue
        side = _side_r(t)
        units = _units_r(t)
        if side in BUY_SIDES:
            holdings[sid] = holdings.get(sid, Decimal(0)) + units
        elif side in SELL_SIDES:
            holdings[sid] = max(Decimal(0), holdings.get(sid, Decimal(0)) - units)
        # IDCW / dividend: no unit change
    return holdings


def build_fund_cashflows(transactions: list[MFTransaction], end_date: str, nav_at_end: Decimal) -> list[tuple]:
    """Build cash-flow list for MF XIRR.

    Buys (incl. switch-in) are outflows (negative); sells (incl. switch-out) and
    IDCW/dividend receipts are inflows (positive). Accepts objects or dicts.
    (Stage 11 audit fix: IDCW sign + switch handling + dict support.)
    """
    flows: list[tuple] = []
    for t in transactions or []:
        d = _date_r(t)
        if not d:
            continue
        side = _side_r(t)
        amt = _amount_r(t)
        if side in BUY_SIDES:
            flows.append((d, -amt, "buy"))
        elif side in SELL_SIDES:
            flows.append((d, amt, "sell"))
        elif side in DIVIDEND_SIDES:
            flows.append((d, amt, "dividend"))
    holdings = build_folio_units(transactions)
    for units in holdings.values():
        if units > 0 and nav_at_end is not None:
            flows.append((end_date, units * nav_at_end, "terminal_value"))
    return flows


def calculate_fund_xirr(transactions: list[MFTransaction], end_date: str, nav_at_end: Decimal | None = None) -> dict:
    """Calculate scheme-level XIRR."""
    if not transactions:
        return {"xirr": None, "annualized": None, "cashflows": [], "reason": "no_transactions"}
    nav_val = nav_at_end or Decimal(0)
    flows = build_fund_cashflows(transactions, end_date, nav_val)
    if len(flows) < 2:
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "insufficient_flows"}
    has_neg = any(a < 0 for _, a, _ in flows)
    has_pos = any(a > 0 for _, a, _ in flows)
    if not (has_neg and has_pos):
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "missing_opposite_sign"}
    try:
        xv = compute_xirr([(d, a) for d, a, _label in flows])
    except Exception:  # noqa: BLE001
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "solver_failed"}
    if xv is None:
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "no_solution"}
    try:
        ann = annualize_pct(xv)
    except Exception:  # noqa: BLE001
        ann = None
    return {"xirr": float(xv), "annualized": ann, "cashflows": flows, "reason": None}


def calculate_portfolio_mf_xirr(portfolio_transactions: list[MFTransaction], end_date: str, nav_data_by_scheme: dict[str, Decimal]) -> dict:
    """Calculate overall MF portfolio XIRR (net of redemptions/switches).

    (Stage 11 audit fix: previous version omitted redemptions from holdings and
    mis-signed IDCW, overstating terminal value and understating returns.)
    """
    all_flows: list[tuple] = []
    for t in portfolio_transactions or []:
        d = _date_r(t)
        if not d:
            continue
        side = _side_r(t)
        amt = _amount_r(t)
        if side in BUY_SIDES:
            all_flows.append((d, -amt, "buy"))
        elif side in SELL_SIDES:
            all_flows.append((d, amt, "sell"))
        elif side in DIVIDEND_SIDES:
            all_flows.append((d, amt, "dividend"))
    holdings = build_folio_units(portfolio_transactions)
    for sid, units in holdings.items():
        nav = _dec(nav_data_by_scheme.get(sid, Decimal(0)))
        if units > 0 and nav > 0:
            all_flows.append((end_date, units * nav, "terminal_value"))
    if len(all_flows) < 2:
        return {"xirr": None, "annualized": None, "cashflows": all_flows, "reason": "insufficient_flows"}
    has_neg = any(a < 0 for _, a, _ in all_flows)
    has_pos = any(a > 0 for _, a, _ in all_flows)
    if not (has_neg and has_pos):
        return {"xirr": None, "annualized": None, "cashflows": all_flows, "reason": "missing_opposite_sign"}
    try:
        xv = compute_xirr([(d, a) for d, a, _label in all_flows])
    except Exception:  # noqa: BLE001
        return {"xirr": None, "annualized": None, "cashflows": all_flows, "reason": "solver_failed"}
    if xv is None:
        return {"xirr": None, "annualized": None, "cashflows": all_flows, "reason": "no_solution"}
    try:
        ann = annualize_pct(xv)
    except Exception:  # noqa: BLE001
        ann = None
    return {"xirr": float(xv), "annualized": ann, "cashflows": all_flows, "reason": None}


def calculate_invested_amount(transactions: list[MFTransaction]) -> Decimal:
    """Total invested amount (purchases/SIPs/bonus allotments)."""
    total = Decimal(0)
    for t in transactions or []:
        if _side_r(t) in BUY_SIDES:
            total += _amount_r(t)
    return total


def calculate_current_value(units: Decimal, nav: Decimal) -> Decimal:
    """Current value = units x NAV."""
    return units * nav


def _fifo_gains_for_scheme(transactions: list, sid: str) -> dict:
    """FIFO cost-basis gains for a single scheme (buys add lots, sells consume FIFO)."""
    lots: list[list[Decimal]] = []  # [units, unit_cost]
    invested = Decimal(0)
    redeemed = Decimal(0)
    realized = Decimal(0)

    def _key(t):
        return str(_date_r(t) or "")

    for t in sorted(transactions or [], key=_key):
        if _sid_r(t) != sid:
            continue
        side = _side_r(t)
        units = _units_r(t)
        amt = _amount_r(t)
        if side in BUY_SIDES:
            invested += amt
            unit_cost = (amt / units) if units > 0 else Decimal(0)
            lots.append([units, unit_cost])
        elif side in SELL_SIDES:
            redeemed += amt  # proceeds (positive)
            remaining = units
            cost_removed = Decimal(0)
            while remaining > 0 and lots:
                lot = lots[0]
                take = min(remaining, lot[0])
                cost_removed += take * lot[1]
                lot[0] -= take
                remaining -= take
                if lot[0] <= 0:
                    lots.pop(0)
            realized += amt - cost_removed
        # dividends/IDCW: no cost effect

    remaining_units = sum((l[0] for l in lots), Decimal(0))
    remaining_cost = sum((l[0] * l[1] for l in lots), Decimal(0))
    return {"invested": invested, "redeemed": redeemed, "realized": realized,
            "remaining_units": remaining_units, "remaining_cost": remaining_cost}


def calculate_realized_unrealized_gains(transactions: list, nav_data: dict[str, Decimal]) -> dict[str, dict]:
    """Calculate realized and unrealized gains per scheme using FIFO cost basis.

    realized_gain = sum(sale proceeds − FIFO cost of units sold); unrealized_gain =
    current value − FIFO cost of remaining units. Accepts objects or dicts.
    (Stage 11 audit fix: previous formula conflated net-invested with cost basis
    and mis-signed redemption proceeds.)
    """
    sids: list[str] = []
    seen: set[str] = set()
    for t in transactions or []:
        sid = _sid_r(t)
        if sid and sid not in seen:
            seen.add(sid)
            sids.append(sid)

    result: dict[str, dict] = {}
    for sid in sids:
        g = _fifo_gains_for_scheme(transactions, sid)
        units = g["remaining_units"]
        nav = _dec(nav_data.get(sid, Decimal(0)))
        current_val = units * nav if nav > 0 else Decimal(0)
        unrealized = current_val - g["remaining_cost"]
        result[sid] = {
            "units": float(units),
            "nav": float(nav) if nav else None,
            "current_value": float(current_val),
            "invested": float(g["invested"]),
            "redeemed": float(g["redeemed"]),
            "realized_gain": float(g["realized"]),
            "unrealized_gain": float(unrealized),
            "missing_nav": nav <= 0,
        }
    return result
