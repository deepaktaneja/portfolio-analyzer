"""MF performance reports (Stage 4)."""
from __future__ import annotations
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional, Dict, List, Any

from models.mf import MFScheme, MFNav, MFTransaction, MFPortfolio
from mf.accounting import build_folio_units, calculate_fund_xirr, calculate_portfolio_mf_xirr, calculate_invested_amount, calculate_current_value, calculate_realized_unrealized_gains


def scheme_holdings_report(transactions, nav_data, schemes=None):
    """Scheme-wise holdings, current value, invested amount, gains."""
    gains = calculate_realized_unrealized_gains(transactions, nav_data)
    invested = {}
    for t in transactions:
        if t.side in ("SIP", "PURCHASE", "LUMP_SUM", "SUBSCRIPTION", "allotment", "bonus", "growth", "accumulation", "reinvest"):
            invested[t.scheme_code] = invested.get(t.scheme_code, Decimal("0")) + (t.amount or Decimal("0"))

    result = {}
    for sid, data in gains.items():
        scheme_info = {}
        if schemes and sid in schemes:
            s = schemes[sid]
            scheme_info = {"scheme_name": s.scheme_name, "plan_type": s.plan_type, "direct_regular": s.direct_regular, "growth_idcw": s.growth_idcw}

        result[sid] = {
            "scheme_code": sid,
            **scheme_info,
            "units": data["units"],
            "nav": data["nav"],
            "current_value": data["current_value"],
            "invested": float(invested.get(sid, Decimal("0"))),
            "realized_gain": data["realized_gain"],
            "unrealized_gain": data["unrealized_gain"],
            "missing_nav": data["missing_nav"],
        }
    return result


def scheme_xirr_report(transactions_by_scheme, end_date, nav_data):
    """Scheme-level XIRR report."""
    result = {}
    for sid, txns in transactions_by_scheme.items():
        nav = nav_data.get(sid, Decimal("0"))
        xirr_res = calculate_fund_xirr(txns, end_date, nav if nav > 0 else None)
        result[sid] = {
            "xirr": xirr_res["xirr"],
            "annualized": xirr_res["annualized"],
            "reason": xirr_res["reason"],
            "cashflow_count": len(xirr_res["cashflows"]),
            "nav_used": float(nav) if nav > 0 else None,
            "missing_nav": nav <= 0,
        }
    return result


def portfolio_mf_report(portfolio_transactions, end_date, nav_data_by_scheme):
    """Consolidated MF portfolio report."""
    xirr_res = calculate_portfolio_mf_xirr(portfolio_transactions, end_date, nav_data_by_scheme)
    gains = calculate_realized_unrealized_gains(portfolio_transactions, nav_data_by_scheme)
    invested = calculate_invested_amount(portfolio_transactions)
    total_current = sum(d["current_value"] for d in gains.values())
    total_invested = float(invested)
    total_realized = sum(d["realized_gain"] for d in gains.values())
    total_unrealized = sum(d["unrealized_gain"] for d in gains.values())
    missing_navs = [sid for sid, d in gains.items() if d["missing_nav"]]
    return {
        "xirr": xirr_res["xirr"],
        "annualized": xirr_res["annualized"],
        "reason": xirr_res["reason"],
        "total_current_value": total_current,
        "total_invested": total_invested,
        "total_realized_gain": total_realized,
        "total_unrealized_gain": total_unrealized,
        "scheme_count": len(gains),
        "missing_nav_schemes": missing_navs,
        "scheme_details": gains,
    }


def reconciliation_report(reconstructed, reported):
    """Reconcile reconstructed units vs reported holdings."""
    all_schemes = set(reconstructed.keys()) | set(reported.keys())
    rows = []
    for sid in sorted(all_schemes):
        r = reconstructed.get(sid, Decimal("0"))
        s = reported.get(sid, Decimal("0"))
        diff = r - s
        rows.append({
            "scheme_code": sid,
            "reconstructed_units": float(r),
            "reported_units": float(s),
            "diff": float(diff),
            "reconciled": abs(diff) < 1e-6,
        })
    return rows
print("mf/reports.py created")
