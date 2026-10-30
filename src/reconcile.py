"""Reconcile computed holdings against the supplied portfolio XLS."""
from __future__ import annotations

import pandas as pd


def reconcile(transactions: list[dict], holdings: pd.DataFrame) -> list[dict]:
    """Compare net quantity from transactions vs XLS holdings. Returns list of mismatch dicts."""
    # net qty per ISIN from transactions
    net: dict[str, float] = {}
    symbol_by_isin: dict[str, str] = {}
    for t in transactions or []:
        isin = t.get("isin")
        if not isin:
            continue
        q = t.get("quantity") or 0
        try:
            q = float(q)
        except (TypeError, ValueError):
            q = 0.0
        if t.get("buy_sell") == "BUY":
            net[isin] = net.get(isin, 0.0) + q
        elif t.get("buy_sell") == "SELL":
            net[isin] = net.get(isin, 0.0) - q
        if t.get("security"):
            symbol_by_isin.setdefault(isin, t.get("security"))

    # XLS quantities by ISIN
    xls_qty: dict[str, float] = {}
    xls_symbol: dict[str, str] = {}
    if holdings is not None and not holdings.empty and "isin" in holdings.columns:
        for _, row in holdings.iterrows():
            isin = row.get("isin")
            if isin is None or (isinstance(isin, float) and pd.isna(isin)):
                continue
            isin = str(isin).strip().upper()
            q = row.get("quantity")
            try:
                xls_qty[isin] = float(q) if q is not None and pd.notna(q) else 0.0
            except (TypeError, ValueError):
                xls_qty[isin] = 0.0
            sym = row.get("symbol")
            if sym is not None and pd.notna(sym):
                xls_symbol[isin] = str(sym).strip()

    mismatches = []
    # Validate every ISIN that appears in either transactions or holdings.
    all_isins = set(net) | set(xls_qty)
    for isin in sorted(all_isins):
        computed = net.get(isin, 0.0)
        held = xls_qty.get(isin, 0.0)
        if abs(computed - held) > 1e-6:
            mismatches.append(
                {
                    "isin": isin,
                    "symbol": xls_symbol.get(isin) or symbol_by_isin.get(isin),
                    "computed_qty": round(computed, 6),
                    "xls_qty": round(held, 6),
                    "diff": round(computed - held, 6),
                }
            )
    return mismatches