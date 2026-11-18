"""ICICI portfolio transaction-history importer (XLS/XLSX/TSV/CSV)."""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

import pandas as pd

from importers.base import NormalizedTransaction, fingerprint_transaction

COLUMN_ALIASES = {
    "symbol": {"stocksymbol", "symbol", "scrip", "scripname"},
    "security": {"companyname", "securityname", "security", "name"},
    "isin": {"isincode", "isin", "isinnumber", "isincodes"},
    "action": {"action", "buysell", "type", "side", "bs"},
    "quantity": {"quantity", "qty", "units", "holdingqty"},
    "price": {"transactionprice", "price", "rate", "tradeprice"},
    "brokerage": {"brokerage"},
    "charges": {"transactioncharges", "charges"},
    "stamp": {"stampduty", "stamp"},
    "date": {"transactiondate", "tradedate", "date"},
    "exchange": {"exchange", "exchangecode"},
    "remarks": {"remarks", "remark", "description"},
}

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _match_columns(df: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set = set()
    for field, aliases in COLUMN_ALIASES.items():
        wanted = {_norm(a) for a in aliases}
        for col in df.columns:
            if col in used:
                continue
            if _norm(col) in wanted:
                mapping[field] = col
                used.add(col)
                break
    return mapping


def _value(rec, mapping, field):
    col = mapping.get(field)
    return rec.get(col) if col is not None else None


def _read_frame(path: str) -> pd.DataFrame:
    lower = str(path).lower()
    if lower.endswith((".xls", ".xlsx")):
        try:
            df = pd.read_excel(path)
            if _match_columns(df):
                return df
        except Exception:  # noqa: BLE001, S110
            pass
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        sample = fh.readline()
    if "\t" in sample:
        return pd.read_csv(path, sep="\t", engine="python")
    if "," in sample:
        return pd.read_csv(path, sep=",", engine="python")
    return pd.read_csv(path, sep=None, engine="python")


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).replace(",", "").replace("`", "").strip()
    if not s or s.lower() in ("nan", "none", "null", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(value: Any) -> str | None:
    s = str(value or "").strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", s)
    if m:
        d, mo, y = m.groups()
        return y + "-" + mo + "-" + d
    return None


def _side(value: Any) -> str | None:
    s = _norm(value)
    if s in ("b", "buy", "bs"):
        return "BUY"
    if s in ("s", "sell"):
        return "SELL"
    return None


def parse_transactions_xls(path: str) -> list[dict]:
    """Parse an ICICI portfolio transaction export into normalized row dicts.
 
    Rows are returned in the same shape produced by src.pdf_parser.parse_pdf so
    they can be stored with SQLiteStore.replace_file_transactions.
    """
    df = _read_frame(path)
    mapping = _match_columns(df)
    if "isin" not in mapping or "quantity" not in mapping:
        raise ValueError("Unrecognised transaction file: ISIN Code / Quantity columns not found")
    name = os.path.basename(path)
    rows: list[dict] = []
    for i, rec in enumerate(df.to_dict("records")):
        isin = str(_value(rec, mapping, "isin") or "").strip().upper()
        if not isin or not ISIN_RE.match(isin):
            continue
        side = _side(_value(rec, mapping, "action"))
        qty = _to_float(_value(rec, mapping, "quantity"))
        if side is None or qty is None or qty == 0:
            continue
        price = _to_float(_value(rec, mapping, "price")) or 0.0
        broker = _to_float(_value(rec, mapping, "brokerage")) or 0.0
        charges = _to_float(_value(rec, mapping, "charges")) or 0.0
        stamp = _to_float(_value(rec, mapping, "stamp")) or 0.0
        gross = qty * price
        fees = broker + charges + stamp
        net = gross + fees if side == "BUY" else gross - fees
        trade_date = _parse_date(_value(rec, mapping, "date"))
        symbol = str(_value(rec, mapping, "symbol") or "").strip() or None
        security = str(_value(rec, mapping, "security") or "").strip() or symbol
        issues = []
        if trade_date is None:
            issues.append("missing_date")
        tx = NormalizedTransaction(
            source_file=name,
            page_number=1,
            row_index=i,
            trade_date=trade_date,
            settlement_date=trade_date,
            exchange=(str(_value(rec, mapping, "exchange") or "").strip() or None),
            order_no=None,
            trade_no=None,
            isin=isin,
            security=security,
            side=side,
            quantity=qty,
            gross_rate=price,
            gross_amount=gross,
            brokerage=broker or None,
            gst=None,
            charges=(charges + stamp) or None,
            net_amount=net,
            confidence=1.0 if not issues else 0.5,
            issues=issues,
            raw_cells=[],
        )
        tx.fingerprint = fingerprint_transaction(tx)
        rows.append({
            "settlement_date": tx.settlement_date,
            "trade_date": tx.trade_date,
            "isin": tx.isin,
            "security": tx.security,
            "buy_sell": tx.side,
            "quantity": tx.quantity,
            "gross_rate": tx.gross_rate,
            "gross_amount": tx.gross_amount,
            "brokerage": tx.brokerage,
            "gst": tx.gst,
            "charges": tx.charges,
            "net_amount": tx.net_amount,
            "exchange": tx.exchange,
            "order_no": tx.order_no,
            "trade_no": tx.trade_no,
            "page_number": tx.page_number,
            "row_index": tx.row_index,
            "source_file": tx.source_file,
            "fingerprint": tx.fingerprint,
            "issues": list(tx.issues),
            "symbol": symbol,
            "remarks": (str(_value(rec, mapping, "remarks") or "").strip() or None),
        })
    return rows
