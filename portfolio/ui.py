"""UI glue for Stage 9 rebalancing: build positions, signals, lots and a cached plan."""
from __future__ import annotations

import streamlit as st

from app.context import AppContext
from portfolio import allocation
from portfolio.config import load_targets
from portfolio.tax_lots import build_fifo_lots


def _equity_positions(holdings) -> list[dict]:
    if holdings is None or getattr(holdings, "empty", True):
        return []
    out = []
    for _, r in holdings.iterrows():
        isin = str(r.get("isin") or "").strip().upper()
        if not isin:
            continue
        try:
            val = float(r.get("current_value") or 0)
        except (TypeError, ValueError):
            val = 0.0
        out.append({
            "security_id": isin,
            "name": str(r.get("symbol") or isin),
            "value": val,
            "asset_class": "Equity",
            "sector": r.get("sector"),
            "market_cap": r.get("market_cap"),
            "price": r.get("current_price"),
            "is_mf": False,
        })
    return out


def _mf_positions(ctx: AppContext) -> list[dict]:
    try:
        from app.mf_ui import get_mf_context
        mf = get_mf_context(ctx)
    except Exception:  # noqa: BLE001
        return []
    if not mf.get("has_mf_data"):
        return []
    out = []
    for row in mf.get("scheme_rows", []):
        code = row.get("scheme_code")
        name = row.get("scheme_name") or code
        out.append({
            "security_id": code,
            "name": name,
            "value": float(row.get("current_value") or 0),
            "asset_class": allocation.classify_asset_class(name, is_mf=True, market_cap=row.get("category")),
            "sector": None,
            "market_cap": row.get("category"),
            "price": row.get("nav"),
            "is_mf": True,
        })
    return out


def _signals_map(ctx: AppContext) -> dict[str, str]:
    """Reuse Stage 8 recommendations already computed on the Holdings page (no heavy recompute here)."""
    recs = st.session_state.get("decision_recs")
    if not recs:
        return {}
    out = {}
    for r in (recs.get("equity", []) + recs.get("mf", [])):
        sid = str(r.get("security_id") or "").strip().upper()
        if sid and r.get("action"):
            out[sid] = r["action"]
    return out


def _compounders(holdings) -> set[str]:
    s = set()
    if holdings is None or getattr(holdings, "empty", True):
        return s
    for _, r in holdings.iterrows():
        isin = str(r.get("isin") or "").strip().upper()
        try:
            q = float(r.get("quantity") or 0)
            avg = float(r.get("avg_price") or 0)
            cv = float(r.get("current_value") or 0)
        except (TypeError, ValueError):
            continue
        if isin and avg > 0 and q > 0:
            gain_pct = (cv - q * avg) / (q * avg) * 100 if q * avg > 0 else 0
            if gain_pct >= 50:  # strong appreciator -> protect unless explicit exit
                s.add(isin)
    return s


def get_rebalance_context(ctx: AppContext, cash_available: float = 0.0) -> dict:
    """Assemble positions, targets, lots & signals; return a cached base (plan recomputed on demand)."""
    positions = _equity_positions(ctx.holdings) + _mf_positions(ctx)
    targets = load_targets()
    # Group transactions by security once (avoids O(positions × transactions) scans).
    by_sid: dict[str, list] = {}
    for t in ctx.txs or []:
        sid = str(t.get("isin") or "").strip().upper()
        if sid:
            by_sid.setdefault(sid, []).append(t)
    lots = {}
    for p in positions:
        if p.get("is_mf"):
            continue
        lots[p["security_id"]] = build_fifo_lots(by_sid.get(p["security_id"], []), p["security_id"])
    return {
        "positions": positions,
        "targets": targets,
        "lots": lots,
        "signals": _signals_map(ctx),
        "compounders": _compounders(ctx.holdings),
        "report": allocation.allocation_report(positions, targets),
    }
