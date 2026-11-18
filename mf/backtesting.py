"""MF/Equity backtesting engine (Stage 5).
Provides stock replacement simulation and historical backtesting capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Any, Tuple

from models.mf import MFTransaction, MFPortfolio
from src.xirr import compute_xirr, annualize_pct

BUY_SIDES = frozenset({
    "SIP", "PURCHASE", "BUY", "LUMP_SUM", "SUBSCRIPTION", "ALLOTMENT",
    "BONUS", "GROWTH", "ACCUMULATION", "REINVEST", "SWITCH_IN", "STP_IN",
})
SELL_SIDES = frozenset({
    "REDEMPTION", "SWITCH_OUT", "SWP", "WITHDRAW", "SELL", "STP_OUT",
})


def load_transaction_history(
    transactions: List[Any], security_id: str
) -> List[Dict[str, Any]]:
    """Load transactions for a security by ISIN/security_id."""
    sid = security_id.strip().upper()
    result = []
    for t in (transactions or []):
        t_sid = (t.get("isin") or t.get("security") or "").strip().upper()
        if t_sid == sid:
            result.append(t)
    return result


def get_transaction_history(
    transactions: List[Any], security_id: str
) -> List[Dict[str, Any]]:
    """Legacy compatibility alias for load_transaction_history."""
    return load_transaction_history(transactions, security_id)


@dataclass
class ReplacementResult:
    """Result of a stock replacement simulation."""
    replaced_transactions: List[Dict[str, Any]] = field(default_factory=list)
    preserved_transactions: List[Dict[str, Any]] = field(default_factory=list)
    replacement_info: Dict[str, Any] = field(default_factory=dict)


def simulate_stock_replacement(
    transactions: List[Dict[str, Any]],
    security_id: str,
    replacement_security_id: str,
    replacement_date: str,
    is_full_replacement: bool = True,
    selected_transaction_indices: Optional[List[int]] = None,
) -> ReplacementResult:
    """Replace Stock A with Stock B using same investment amount and purchase dates.

    Replaces all or selected transactions of security_id with equivalent
    purchases of replacement_security_id. Preserves unaffected transactions.

    Parameters:
    - transactions: list of transaction dicts
    - security_id: ISIN/security_id to replace
    - replacement_security_id: ISIN/security_id to buy instead
    - replacement_date: date string for the replacement (sale) date
    - is_full_replacement: if True, replace all transactions for security_id
    - selected_transaction_indices: list of indices to replace

    Returns ReplacementResult with replaced, preserved, and replacement_info.
    """
    original_tx = get_transaction_history(transactions, security_id)
    if not original_tx:
        return ReplacementResult(
            replacement_info={
                "error": f"No transactions found for {security_id}",
                "security_id": security_id,
            }
        )

    # Determine which transactions to replace
    all_indices = list(range(len(original_tx)))
    if is_full_replacement:
        indices_to_replace = set(all_indices)
    elif selected_transaction_indices:
        indices_to_replace = set(selected_transaction_indices)
    else:
        indices_to_replace = set()

    replaced_tx = []
    preserved_tx = list(transactions)  # start with all transactions

    for idx in sorted(indices_to_replace, reverse=True):
        if idx >= len(original_tx):
            continue
        orig = original_tx[idx]
        # Create replacement sell order for original stock
        sell_tx = {
            "isin": security_id,
            "security": security_id,
            "trade_date": replacement_date,
            "side": "REDEMPTION",  # or SELL
            "units": orig.get("units", 0),
            "amount": orig.get("amount", 0),
            "net_amount": orig.get("amount", 0),
            "price": orig.get("price", 0),
            "source": "backtesting_replacement",
            "replaced_from": orig.get("trade_date"),
            "original_side": orig.get("side"),
        }
        replaced_tx.append(sell_tx)

        # Create replacement buy order for new stock
        buy_tx = {
            "isin": replacement_security_id,
            "security": replacement_security_id,
            "trade_date": orig.get("trade_date"),  # keep original purchase date
            "side": "PURCHASE",
            "units": orig.get("units", 0),
            "amount": orig.get("amount", 0),
            "net_amount": orig.get("amount", 0),
            "price": orig.get("price", 0),
            "source": "backtesting_replacement",
            "original_security": security_id,
            "replacement_date": replacement_date,
        }
        replaced_tx.append(buy_tx)

    # Build preserved list: remove replaced transactions
    original_set = set(id(t) for t in original_tx)
    replaced_in_original = set()
    for idx in indices_to_replace:
        if idx < len(original_tx):
            replaced_in_original.add(id(original_tx[idx]))

    final_preserved = [
        t for t in preserved_tx
        if id(t) not in replaced_in_original
    ]

    return ReplacementResult(
        replaced_transactions=replaced_tx,
        preserved_transactions=final_preserved,
        replacement_info={
            "original_security": security_id,
            "replacement_security": replacement_security_id,
            "replacement_date": replacement_date,
            "is_full_replacement": is_full_replacement,
            "replaced_count": len(indices_to_replace),
            "preserved_count": len(final_preserved),
        },
    )


def build_backtest_cashflows(
    transactions: List[Dict[str, Any]],
    end_date: str,
    price_lookup: callable,
    include_terminal_value: bool = True,
    include_dividends: bool = True,
    brokerage: Decimal = Decimal("0"),
    taxes: Decimal = Decimal("0"),
) -> List[Tuple[str, float, str]]:
    """Build cash-flow list for backtest XIRR calculation."""
    flows: List[Tuple[str, float, str]] = []

    for t in transactions:
        d = t.get("trade_date") or t.get("settlement_date")
        if not d:
            continue

        side = str(t.get("side", "")).upper()
        amt = float(t.get("amount") or t.get("net_amount") or 0)
        qty = float(t.get("units") or t.get("quantity") or 0)

        # Apply brokerage and taxes
        net_amt = amt
        if side in BUY_SIDES:
            net_amt = amt + float(brokerage) + float(taxes)
            flows.append((d, -net_amt, "buy"))
        elif side in SELL_SIDES:
            net_amt = amt - float(brokerage) - float(taxes)
            flows.append((d, net_amt, "sell"))
        elif side == "IDCW" and include_dividends:
            flows.append((d, amt, "dividend"))

    # Add terminal value if requested
    if include_terminal_value:
        from mf.accounting import build_folio_units
        # Try to reconstruct holdings from transactions
        holdings = {}
        for t in transactions:
            sid = t.get("isin") or t.get("security")
            side = str(t.get("side", "")).upper()
            qty = float(t.get("units") or t.get("quantity") or 0)
            if side in BUY_SIDES:
                holdings[sid] = holdings.get(sid, 0) + qty
            elif side in SELL_SIDES:
                holdings[sid] = max(0, holdings.get(sid, 0) - qty)

        for sid, units in holdings.items():
            if units > 0:
                try:
                    px = price_lookup(sid, end_date)
                    if px:
                        flows.append((end_date, units * float(px), "terminal_value"))
                except Exception:
                    # Skip if price lookup fails - flagged as missing data
                    pass

    return flows


def calculate_backtest_xirr(
    transactions: List[Dict[str, Any]],
    end_date: str,
    price_lookup: callable,
    include_terminal_value: bool = True,
    include_dividends: bool = True,
    brokerage: Decimal = Decimal("0"),
    taxes: Decimal = Decimal("0"),
) -> Dict[str, Any]:
    """Calculate XIRR for backtest scenario.

    Returns dict with xirr, annualized%, cashflows, reason, total_invested,
    total_terminal_value, missing_data flags.
    """
    flows = build_backtest_cashflows(
        transactions, end_date, price_lookup,
        include_terminal_value, include_dividends, brokerage, taxes,
    )

    if len(flows) < 2:
        return {
            "xirr": None, "annualized": None, "cashflows": flows,
            "reason": "insufficient_flows",
            "total_invested": 0.0, "total_terminal": 0.0,
            "missing_data": False,
        }

    has_neg = any(a < 0 for _, a, _ in flows)
    has_pos = any(a > 0 for _, a, _ in flows)
    if not (has_neg and has_pos):
        return {
            "xirr": None, "annualized": None, "cashflows": flows,
            "reason": "missing_opposite_sign",
            "total_invested": 0.0, "total_terminal": 0.0,
            "missing_data": False,
        }

    try:
        xv = compute_xirr([(d, a) for d, a, _ in flows])
    except Exception:
        return {
            "xirr": None, "annualized": None, "cashflows": flows,
            "reason": "solver_failed",
            "total_invested": 0.0, "total_terminal": 0.0,
            "missing_data": False,
        }

    if xv is None:
        return {
            "xirr": None, "annualized": None, "cashflows": flows,
            "reason": "no_solution",
            "total_invested": 0.0, "total_terminal": 0.0,
            "missing_data": False,
        }

    try:
        ann = annualize_pct(xv)
    except Exception:
        ann = None

    # Compute totals
    total_invested = sum(
        abs(a) for _, a, l in flows if l == "buy"
    )
    total_terminal = sum(
        a for _, a, l in flows if l == "terminal_value"
    )

    # Check for missing prices (terminal value missing but holdings exist)
    missing_data = include_terminal_value and total_terminal == 0 and any(
        l == "buy" for _, _, l in flows
    )

    return {
        "xirr": float(xv), "annualized": ann, "cashflows": flows,
        "reason": None,
        "total_invested": total_invested, "total_terminal": total_terminal,
        "missing_data": missing_data,
    }


def calculate_terminal_value(
    transactions: List[Dict[str, Any]],
    nav_data: Dict[str, Decimal],
) -> Dict[str, Decimal]:
    """Calculate terminal market value per security from holdings."""
    from mf.accounting import build_folio_units
    holdings = build_folio_units(transactions)
    result = {}
    for sid, units in holdings.items():
        nav = nav_data.get(sid, Decimal("0"))
        result[sid] = units * nav if nav > 0 else Decimal("0")
    return result


def calculate_realized_unrealized_gains(
    transactions: List[Dict[str, Any]],
    nav_data: Dict[str, Decimal],
) -> Dict[str, Dict[str, Any]]:
    """FIFO cost-basis realized/unrealized gains (delegates to mf.accounting).

    Stage 11 audit: previous implementation mis-signed redemption proceeds and
    treated net-invested as cost basis. Now consistent with mf.accounting.
    """
    from mf.accounting import calculate_realized_unrealized_gains as _fifo_gains
    return _fifo_gains(transactions, nav_data)


def compare_portfolios(
    original_transactions: List[Dict[str, Any]],
    hypothetical_transactions: List[Dict[str, Any]],
    end_date: str,
    price_lookup: callable,
    nav_data_by_security: Dict[str, Decimal],
    brokerage: Decimal = Decimal("0"),
    taxes: Decimal = Decimal("0"),
) -> Dict[str, Any]:
    """Compare actual vs hypothetical portfolio performance.

    Calculates actual and hypothetical XIRR, terminal values, and gains.
    Returns comparison dict with differences.
    """
    original_res = calculate_backtest_xirr(
        original_transactions, end_date, price_lookup,
        brokerage=brokerage, taxes=taxes,
    )
    hypothetical_res = calculate_backtest_xirr(
        hypothetical_transactions, end_date, price_lookup,
        brokerage=brokerage, taxes=taxes,
    )

    original_gains = calculate_realized_unrealized_gains(
        original_transactions, nav_data_by_security
    )
    hypothetical_gains = calculate_realized_unrealized_gains(
        hypothetical_transactions, nav_data_by_security
    )

    orig_terminal = original_res.get("total_terminal", 0)
    hyp_terminal = hypothetical_res.get("total_terminal", 0)
    orig_invested = original_res.get("total_invested", 0)
    hyp_invested = hypothetical_res.get("total_invested", 0)

    orig_xirr = original_res.get("xirr") or 0
    hyp_xirr = hypothetical_res.get("xirr") or 0

    return {
        "original": original_res,
        "hypothetical": hypothetical_res,
        "comparison": {
            "xirr_difference": (hyp_xirr - orig_xirr) if (orig_xirr and hyp_xirr) else None,
            "terminal_value_difference": hyp_terminal - orig_terminal,
            "invested_difference": hyp_invested - orig_invested,
            "original_xirr": orig_xirr,
            "hypothetical_xirr": hyp_xirr,
            "original_terminal": orig_terminal,
            "hypothetical_terminal": hyp_terminal,
            "original_invested": orig_invested,
            "hypothetical_invested": hyp_invested,
            "original_gains": original_gains,
            "hypothetical_gains": hypothetical_gains,
        },
        "missing_data": original_res.get("missing_data", False)
        or hypothetical_res.get("missing_data", False),
    }


def calculate_portfolio_mf_xirr(
    portfolio_transactions: List[MFTransaction],
    end_date: str,
    nav_data_by_security: Dict[str, Decimal],
    brokerage: Decimal = Decimal("0"),
    taxes: Decimal = Decimal("0"),
) -> Dict[str, Any]:
    """Calculate consolidated MF portfolio XIRR."""
    flows = []
    for t in portfolio_transactions:
        d = t.trade_date
        if not d:
            continue
        side = str(t.side).upper()
        amt = Decimal(str(t.amount or t.net_amount or 0))
        net_amt = amt
        if side in BUY_SIDES:
            net_amt = amt + brokerage + taxes
            flows.append((d, -float(net_amt), "buy"))
        elif side in SELL_SIDES:
            net_amt = amt - brokerage - taxes
            flows.append((d, float(net_amt), "sell"))
        elif side == "IDCW":
            flows.append((d, float(amt), "dividend"))

    # Terminal values
    holdings = {}
    for t in portfolio_transactions:
        sid = t.scheme_code
        side = str(t.side).upper()
        units = Decimal(str(t.units or 0))
        if side in BUY_SIDES:
            holdings[sid] = holdings.get(sid, Decimal("0")) + units
        elif side in SELL_SIDES:
            holdings[sid] = max(Decimal("0"), holdings.get(sid, Decimal("0")) - units)

    for sid, units in holdings.items():
        nav = nav_data_by_security.get(sid, Decimal("0"))
        if units > 0 and nav > 0:
            flows.append((end_date, float(units * nav), "terminal_value"))

    if len(flows) < 2:
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "insufficient_flows"}

    has_neg = any(a < 0 for _, a, _ in flows)
    has_pos = any(a > 0 for _, a, _ in flows)
    if not (has_neg and has_pos):
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "missing_opposite_sign"}

    try:
        xv = compute_xirr([(d, a) for d, a, _ in flows])
    except Exception:
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "solver_failed"}

    if xv is None:
        return {"xirr": None, "annualized": None, "cashflows": flows, "reason": "no_solution"}

    try:
        ann = annualize_pct(xv)
    except Exception:
        ann = None

    return {
        "xirr": float(xv), "annualized": ann, "cashflows": flows, "reason": None,
    }
