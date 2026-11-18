"""UI helpers for MF analytics: cached context loading + combined-exposure view."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.context import AppContext


def equity_weights(holdings) -> tuple[dict[str, float], float]:
    """Return ({isin: weight_pct_of_equity}, equity_total_value)."""
    if holdings is None or getattr(holdings, "empty", True):
        return {}, 0.0
    total = 0.0
    rows = []
    for _, r in holdings.iterrows():
        try:
            cv = float(r.get("current_value") or 0)
        except (TypeError, ValueError):
            cv = 0.0
        isin = str(r.get("isin") or "").strip().upper()
        rows.append((isin, cv))
        total += cv
    weights = {isin: (cv / total * 100 if total > 0 and isin else 0.0) for isin, cv in rows}
    return weights, total


def _slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "person"


def get_mf_context(ctx: AppContext, portfolio_folder: str = "data/mf") -> dict:
    """Load (and cache per session) the MF analytics context."""
    direct_weights, _ = equity_weights(ctx.holdings)
    from pathlib import Path

    from app.shared import resolve_person_name

    person = resolve_person_name(
        getattr(ctx, "xirr_db_path", "data/xirr.sqlite3"),
        getattr(ctx.filters, "person_id", None),
        getattr(ctx.filters, "person_name", None),
    )
    folder = portfolio_folder
    if person:
        folder = str(Path(portfolio_folder) / _slug(person))
    key = "mf_context_" + (_slug(person) if person else "all") + "-" + str(sum(1 for _r,_d,_fs in __import__("os").walk(folder) for _f in _fs)) + "-" + str(getattr(ctx.filters, "end_date", ""))
    if key not in st.session_state:
        try:
            from mf.service import load_mf_context
            st.session_state[key] = load_mf_context(
                folder=folder,
                fetch_nav=False,
                direct_holdings=direct_weights,
                end_date=str(ctx.filters.end_date) if ctx.filters.end_date else None,
            )
        except Exception as e:  # noqa: BLE001
            st.session_state[key] = {"has_mf_data": False, "error": str(e)}
    return st.session_state[key]


def combined_exposure_view(ctx: AppContext, mf: dict) -> list[dict]:
    """Return combined direct+indirect exposure as % of the total (equity+MF) portfolio."""
    from mf.overlap import combine_with_direct_equity

    direct_weights, equity_total = equity_weights(ctx.holdings)
    mf_total = float(mf.get("total_value") or 0.0)
    combined_total = equity_total + mf_total
    if combined_total <= 0:
        return []
    mf_share = mf_total / combined_total
    indirect_scaled: dict[str, dict] = {}
    for isin, rec in (mf.get("indirect_exposure") or {}).items():
        indirect_scaled[isin] = {
            "stock_name": rec.get("stock_name"),
            "sector": rec.get("sector"),
            "market_cap": rec.get("market_cap"),
            "indirect_pct": float(rec.get("indirect_pct") or 0.0) * mf_share,
        }
    direct_scaled = {isin: w * equity_total / combined_total for isin, w in direct_weights.items()}
    return combine_with_direct_equity(indirect_scaled, direct_scaled)


def scheme_table(mf: dict) -> pd.DataFrame:
    """Flatten per-scheme rows into a display DataFrame (analytics summarised)."""
    rows = []
    for r in mf.get("scheme_rows", []):
        s = r.get("analytics", {}) or {}
        tr = s.get("trailing_returns", {}) or {}
        rows.append({
            "scheme_code": r.get("scheme_code"),
            "scheme_name": r.get("scheme_name"),
            "category": r.get("category"),
            "units": r.get("units"),
            "nav": r.get("nav"),
            "current_value": r.get("current_value"),
            "invested": r.get("invested"),
            "unrealized_gain": r.get("unrealized_gain"),
            "xirr": r.get("xirr"),
            "1y": tr.get("1y"),
            "3y": tr.get("3y"),
            "5y": tr.get("5y"),
            "volatility": s.get("annualized_volatility"),
            "max_drawdown": (s.get("max_drawdown") or {}).get("max_drawdown"),
            "sharpe": s.get("sharpe_ratio"),
            "nav_status": r.get("nav_status"),
        })
    return pd.DataFrame(rows)
