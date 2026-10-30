"""Load ICICI portfolio XLS holdings."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pandas as pd


def _normalize_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


COLUMN_ALIASES = {
    "isin": {"isin", "isincode", "isinnumber", "isincodes"},
    "symbol": {
        "symbol", "scripname", "securityname", "security", "name",
        "stocksymbol", "stock",
    },
    "quantity": {"quantity", "qty", "holdingqty", "currentqty", "units"},
    "avg_price": {
        "avgprice", "averageprice", "buyavg", "avgcost", "averagecost",
        "averagecostprice", "avgcostprice", "averagebuyprice",
    },
    "current_value": {
        "currentvalue", "marketvalue", "value", "valuation",
        "valueatmarketprice", "valueatmarket", "marketpricevalue",
    },
    "current_price": {
        "currentprice", "marketprice", "ltp", "lastprice",
        "currentmarketprice",
    },
}

PORTFOLIO_EXTENSIONS = {".xls", ".xlsx", ".csv", ".tsv"}


def discover_portfolio_files(folder: str | os.PathLike) -> list[str]:
    """Return sorted list of portfolio files (XLS/CSV/TSV) in a folder."""
    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        return []
    files = [
        str(p) for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in PORTFOLIO_EXTENSIONS
    ]
    files.sort(key=lambda p: Path(p).name)
    return files


def _read_sheets(path: str) -> dict:
    """Read a portfolio file into a dict of sheet-name -> DataFrame."""
    try:
        return pd.read_excel(path, sheet_name=None, engine="openpyxl")
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        return pd.read_excel(path, sheet_name=None)
    except Exception:  # noqa: BLE001, S110
        pass
    for sep in ("\t", ","):
        try:
            df = pd.read_csv(path, sep=sep, engine="python")
            if df is not None and df.shape[1] > 1:
                return {"Sheet1": df}
        except Exception:  # noqa: BLE001, S112
            continue
    raise ValueError(f"Could not read portfolio file: {path}")


def _detect_columns(df: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set = set()
    for field, aliases in COLUMN_ALIASES.items():
        best_col = None
        best_score = 0
        for col in df.columns:
            if col in used:
                continue
            norm = _normalize_col(col)
            if not norm:
                continue
            for a in aliases:
                if not a:
                    continue
                if norm == a:
                    score = len(a) + 1000
                elif a in norm or norm in a:
                    score = len(a)
                else:
                    score = 0
                if score > best_score:
                    best_score = score
                    best_col = col
        if best_col is not None and best_score > 0:
            mapping[field] = best_col
            used.add(best_col)
    return mapping


def load_holdings(path: str | os.PathLike) -> pd.DataFrame:
    """Return a normalized DataFrame with columns: isin, symbol, quantity, avg_price,
    current_value, current_price. Missing columns are filled with None.

    ``path`` may be either a single portfolio file or a folder containing one.
    """
    if os.path.isdir(path):
        files = discover_portfolio_files(path)
        if not files:
            raise ValueError(f"No portfolio files found in folder: {path}")
        path = files[0]

    from importers.xls.icici_portfolio import parse_portfolio

    df, meta = parse_portfolio(str(path))
    out = pd.DataFrame()
    out["isin"] = df.get("isin")
    out["symbol"] = df.get("symbol")
    if "security" in df.columns:
        out["symbol"] = out["symbol"].fillna(df.get("security"))
    out["quantity"] = df.get("quantity")
    out["avg_price"] = df.get("avg_price")
    if "market_value" in df.columns:
        out["current_value"] = df.get("market_value")
    elif "cost_value" in df.columns:
        out["current_value"] = df.get("cost_value")
    else:
        out["current_value"] = None
    out["current_price"] = df.get("current_price")
    if "isin" in out.columns:
        out["isin"] = out["isin"].astype(str).str.strip().str.upper()
        out.loc[out["isin"].isin({"NAN", "NONE", "NAT"}), "isin"] = None
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].astype(str).str.strip()
        out.loc[out["symbol"].isin({"NAN", "NONE", "NAT"}), "symbol"] = None
    for c in ("quantity", "avg_price", "current_value", "current_price"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out[out["isin"].notna() & (out["isin"] != "NAN") & (out["isin"] != "NONE")]
    out = out.reset_index(drop=True)
    out.attrs["as_of_date"] = meta.get("as_of_date")
    out.attrs["detected_type"] = meta.get("detected_type")
    return out

# Backwards/alternative names for the portfolio folder helpers.
discover_portfolio = discover_portfolio_files
discover_portfolios = discover_portfolio_files
load_portfolio = load_holdings


def current_value_for(row: Any) -> float | None:
    if row.get("current_value") is not None and pd.notna(row.get("current_value")):
        return float(row["current_value"])
    q = row.get("quantity")
    p = row.get("current_price")
    if pd.notna(q) and pd.notna(p):
        return float(q) * float(p)
    return None