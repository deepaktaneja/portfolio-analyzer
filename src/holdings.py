"""Load ICICI portfolio XLS holdings."""
from __future__ import annotations

import re
from typing import Any

import pandas as pd


def _normalize_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


COLUMN_ALIASES = {
    "isin": {"isin", "isincode", "isinnumber"},
    "symbol": {"symbol", "scripname", "securityname", "security", "name"},
    "quantity": {"quantity", "qty", "holdingqty", "currentqty", "units"},
    "avg_price": {"avgprice", "averageprice", "buyavg", "avgcost", "averagecost"},
    "current_value": {"currentvalue", "marketvalue", "value", "valuation"},
    "current_price": {"currentprice", "marketprice", "ltp", "lastprice"},
}


def _detect_columns(df: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for col in df.columns:
        norm = _normalize_col(col)
        for field, aliases in COLUMN_ALIASES.items():
            if norm in aliases and field not in mapping:
                mapping[field] = col
    return mapping


def load_holdings(path: str) -> pd.DataFrame:
    """Return a normalized DataFrame with columns: isin, symbol, quantity, avg_price,
    current_value, current_price. Missing columns are filled with None."""
    # Try all sheets; pick the one with the most recognized columns.
    try:
        sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    except Exception:
        sheets = pd.read_excel(path, sheet_name=None)
    best = None
    best_score = -1
    for name, df in sheets.items():
        m = _detect_columns(df)
        if len(m) > best_score:
            best_score = len(m)
            best = (name, df, m)
    if best is None:
        raise ValueError("No sheets found in XLS")
    _, df, mapping = best
    out = pd.DataFrame()
    for field in ("isin", "symbol", "quantity", "avg_price", "current_value", "current_price"):
        if field in mapping:
            out[field] = df[mapping[field]]
        else:
            out[field] = None
    # Clean
    if "isin" in out.columns:
        out["isin"] = out["isin"].astype(str).str.strip().str.upper()
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].astype(str).str.strip()
    for c in ("quantity", "avg_price", "current_value", "current_price"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    # Drop rows without ISIN
    out = out[out["isin"].notna() & (out["isin"] != "NAN") & (out["isin"] != "NONE")]
    out = out.reset_index(drop=True)
    return out


def current_value_for(row: Any) -> float | None:
    if row.get("current_value") is not None and pd.notna(row.get("current_value")):
        return float(row["current_value"])
    q = row.get("quantity")
    p = row.get("current_price")
    if pd.notna(q) and pd.notna(p):
        return float(q) * float(p)
    return None