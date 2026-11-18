"""Position reconstruction helpers for transaction filtering."""
from __future__ import annotations

from collections import defaultdict


def signed_quantity(row) -> float:
    qty = float(row.get("quantity") or 0.0)
    side = str(row.get("buy_sell") or "").upper()
    if side == "BUY":
        return qty
    if side == "SELL":
        return -qty
    return 0.0


def net_positions(rows) -> dict[str, float]:
    pos: dict[str, float] = defaultdict(float)
    for r in rows:
        isin = str(r.get("isin") or "").strip().upper()
        if isin:
            pos[isin] += signed_quantity(r)
    return dict(pos)


def fully_sold_isins(rows, baseline=None, tol: float = 1e-6) -> set[str]:
    """ISINs whose net position is zero and that have at least one SELL in rows."""
    pos: dict[str, float] = defaultdict(float)
    for r in baseline or []:
        isin = str(r.get("isin") or "").strip().upper()
        if isin:
            pos[isin] += signed_quantity(r)
    sold = set()
    for r in rows:
        isin = str(r.get("isin") or "").strip().upper()
        if not isin:
            continue
        pos[isin] += signed_quantity(r)
        if str(r.get("buy_sell") or "").upper() == "SELL":
            sold.add(isin)
    return {isin for isin, q in pos.items() if abs(q) <= tol and isin in sold}


def filter_fully_sold(rows, baseline=None, tol: float = 1e-6):
    """Return (kept_rows, fully_sold_isins) for the given candidate rows."""
    keep = fully_sold_isins(rows, baseline, tol)
    kept = [r for r in rows if str(r.get("isin") or "").strip().upper() in keep]
    return kept, keep
