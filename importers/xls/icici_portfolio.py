"""ICICI portfolio XLS/XLSX/HTML/TSV importer (Stage 01)."""
from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

import pandas as pd

from importers.detection import detect_source, detect_tabular

COLUMN_ALIASES = {
    "symbol": {"symbol", "stocksymbol", "scrip", "scripname"},
    "security": {"companyname", "securityname", "security", "name"},
    "isin": {"isin", "isincode", "isincodes", "isincodenumber"},
    "quantity": {"qty", "quantity", "units", "holdingqty"},
    "avg_price": {"averagecostprice", "avgcostprice", "averageprice", "avgprice", "averagecost"},
    "current_price": {"currentmarketprice", "marketprice", "ltp", "currentprice"},
    "cost_value": {"valueatcost", "costvalue", "investmentvalue"},
    "market_value": {"valueatmarketprice", "marketvalue", "value", "valuation"},
}


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def detect_file_type(path: str) -> str:
    return detect_tabular(path)


def _read_frame(path: str, dtype: str) -> pd.DataFrame:
    if dtype == "xlsx":
        return pd.read_excel(path, engine="openpyxl")
    if dtype == "xls_ole2":
        return pd.read_excel(path, engine="xlrd")
    if dtype == "html":
        tables = pd.read_html(path)
        if not tables:
            raise ValueError("no tables in html")
        return tables[0]
    # tsv/csv/text
    for sep in ("\t", ","):
        try:
            df = pd.read_csv(path, sep=sep, engine="python")
            if df is not None and df.shape[1] > 1:
                return df
        except Exception:  # noqa: BLE001, S112
            continue
    raise ValueError(f"could not read tabular file: {path}")


def _detect_columns(df: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set = set()
    for field, aliases in COLUMN_ALIASES.items():
        best_col = None
        best_score = 0
        for col in df.columns:
            if col in used:
                continue
            norm = _norm(col)
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


def _as_of_date(path: str, df: pd.DataFrame) -> str | None:
    # try filename
    m = re.search(r"(\d{2})[-_](\d{2})[-_](\d{4})", os.path.basename(path))
    if m:
        d, mo, y = m.groups()
        try:
            return date(int(y), int(mo), int(d)).isoformat()
        except ValueError:
            pass
    # scan first rows for date-like
    for _, row in df.head(5).iterrows():
        for v in row.tolist():
            s = str(v or "")
            m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", s)
            if m and ("as on" in s.lower() or "date" in s.lower()):
                d, mo, y = m.groups()
                try:
                    return date(int(y), int(mo), int(d)).isoformat()
                except ValueError:
                    pass
    return None


def parse_portfolio(path: str) -> tuple[pd.DataFrame, dict]:
    dtype = detect_file_type(path)
    df = _read_frame(path, dtype)
    # if header not in first row, try to find header row
    header_row = 0
    for i in range(min(10, len(df))):
        row_vals = [str(x).lower() for x in df.iloc[i].tolist()]
        joined = " ".join(row_vals)
        if "isin" in joined and ("qty" in joined or "quantity" in joined):
            header_row = i
            break
    if header_row > 0:
        df = pd.read_csv(path, sep="\t" if dtype == "tsv" else ",", skiprows=header_row) if dtype in ("tsv", "csv", "text") else df
    mapping = _detect_columns(df)
    out = pd.DataFrame()
    for field in ("symbol", "security", "isin", "quantity", "avg_price", "current_price", "cost_value", "market_value"):
        if field in mapping:
            out[field] = df[mapping[field]]
        else:
            out[field] = None
    if "isin" in out.columns:
        out["isin"] = out["isin"].astype(str).str.strip().str.upper()
        out.loc[out["isin"].isin({"NAN", "NONE", "NAT"}), "isin"] = None
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].astype(str).str.strip()
        out.loc[out["symbol"].isin({"NAN", "NONE", "NAT"}), "symbol"] = None
    for c in ("quantity", "avg_price", "current_price", "cost_value", "market_value"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out[out["isin"].notna() & (out["isin"] != "NAN") & (out["isin"] != "NONE")]
    out = out.reset_index(drop=True)
    meta = {"detected_type": dtype, "as_of_date": _as_of_date(path, df), "columns": list(df.columns)}
    return out, meta


def parse_document(path: str):

    doc = detect_source(path)
    df, meta = parse_portfolio(path)
    return doc, df, meta