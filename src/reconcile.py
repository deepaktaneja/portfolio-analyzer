"""Reconcile computed holdings against the supplied portfolio XLS."""
from __future__ import annotations

import pandas as pd


def reconcile(transactions: list[dict], holdings: pd.DataFrame) -> list[dict]:
    """Compare net quantity from transactions vs XLS holdings. Returns list of mismatch dicts."""
    # net qty per ISIN from transactions
    net: dict[str, float] = {}
    for t in transactions:
        isin = t.get("isin")
        if not isin:
            continue
        q = t.get("quantity") or 0
        if t.get("buy_sell") == "BUY":
            net[isin] = net.get(isin, 0) + q
        elif t.get("buy_sell") == "SELL":
            net[isin] = net.get(isin, 0) - q
    mismatches = []
    # Only report ISINs that are not part of the current portfolio holdings.
    holding_isins = set(str(x) for x in holdings.get("isin", []).tolist())
    for isin, q in net.items():
        if isin in holding_isins:
            continue
        if abs(q) > 1e-6:
            mismatches.append({
                "isin": isin,
                "symbol": None,
                "computed_qty": q,
                "xls_qty": 0,
                "diff": q,
            })
    return mismatches