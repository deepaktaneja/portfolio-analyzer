"""ICICI portfolio transaction-history importer (XLS/XLSX/TSV/CSV)."""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

import pandas as pd

from importers.base import NormalizedTransaction, fingerprint_transaction

COLUMN_ALIASES = {
    "symbol": {"stocksymbol", "symbol", "scrip", "scripname", "stock"},
    "security": {"companyname", "securityname", "security", "name"},
    "isin": {"isincode", "isin", "isinnumber", "isincodes"},
    "action": {"action", "buysell", "type", "side", "bs"},
    "quantity": {"quantity", "qty", "units", "holdingqty"},
    "price": {"transactionprice", "price", "rate", "tradeprice"},
    "brokerage": {"brokerage", "brokerageservicetax", "brokerservicetax", "brokerageincltaxes"},
    "charges": {"transactioncharges", "charges"},
    "stamp": {"stampduty", "stamp"},
    "stt": {"stt"},
    "turnover_charges": {"transactionandsebiturnturnovercharges", "turnovercharges"},
    "date": {"transactiondate", "tradedate", "date"},
    "exchange": {"exchange", "exchangecode"},
    "remarks": {"remarks", "remark", "description"},
    "order_no": {"orderno", "orderref"},
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
    # ``utf-8-sig`` transparently strips a UTF-8 BOM (ICICI order-book CSV
    # exports start with one) and still reads plain UTF-8 unchanged.
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        sample = fh.readline()
    if "\t" in sample:
        return pd.read_csv(path, sep="\t", engine="python", encoding="utf-8-sig")
    if "," in sample:
        return pd.read_csv(path, sep=",", engine="python", encoding="utf-8-sig")
    return pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")


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
    name = os.path.basename(path)

    # ---- ICICI Direct Equity Order Book CSV (no ISIN column) ----------------
    if (
        "symbol" in mapping
        and "action" in mapping
        and "quantity" in mapping
        and "price" in mapping
        and "date" in mapping
        and "isin" not in mapping
    ):
        rows: list[dict] = []
        # Prefer the all-inclusive "Brokerage Incl. Taxes" column: it already
        # absorbs STT/stamp/turnover, so those are not added on top again.
        broker_col = next(
            (c for c in df.columns if _norm(c) == "brokerageincltaxes"),
            mapping.get("brokerage"),
        )
        for i, rec in enumerate(df.to_dict("records")):
            order_no = str(_value(rec, mapping, "order_no") or "").strip() or None
            side = _side(_value(rec, mapping, "action"))
            qty = _to_float(_value(rec, mapping, "quantity"))
            price = _to_float(_value(rec, mapping, "price"))
            trade_date = _parse_date(_value(rec, mapping, "date"))
            stock = str(_value(rec, mapping, "symbol") or "").strip()
            exchange = str(_value(rec, mapping, "exchange") or "").strip()
            if side is None or qty is None or qty <= 0 or price is None or price <= 0:
                # Cancelled / Rejected / Pending / Open / invalid: skip so
                # re-uploading overlapping statements stays idempotent.
                continue
            ticker = stock.upper()
            if "." not in ticker:
                exch_upper = exchange.upper()
                suffix = {"NSE": ".NS", "BSE": ".BO"}.get(
                    exch_upper, f".{exch_upper}" if exch_upper else ".NS"
                )
                ticker = ticker + suffix
            gross = qty * price
            broker = _to_float(rec.get(broker_col)) or 0.0
            fees = broker  # Brokerage Incl. Taxes already absorbs STT/stamp/turnover
            net = gross + fees if side == "BUY" else gross - fees
            issues: list[str] = []
            if trade_date is None:
                issues.append("missing_date")
            if not stock:
                issues.append("missing_ticker")
            tx = NormalizedTransaction(
                source_file=name,
                page_number=1,
                row_index=i,
                trade_date=trade_date,
                settlement_date=trade_date,
                exchange=exchange,
                order_no=order_no,
                trade_no=None,
                isin=None,
                security=ticker,
                side=side,
                quantity=qty,
                gross_rate=price,
                gross_amount=gross,
                brokerage=broker or None,
                gst=None,
                charges=None,
                net_amount=net,
                confidence=1.0 if not issues else 0.5,
                issues=issues,
                raw_cells=[],
            )
            tx.fingerprint = fingerprint_transaction(tx)
            rows.append({
                "settlement_date": tx.settlement_date,
                "trade_date": tx.trade_date,
                "isin": None,
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
                "symbol": stock.upper() or None,
                "remarks": (str(_value(rec, mapping, "remarks") or "").strip() or None),
            })
        return rows

    # ---- Legacy ICICI portfolio transaction export (ISIN Code required) -----
    if "isin" not in mapping or "quantity" not in mapping:
        raise ValueError("Unrecognised transaction file: ISIN Code / Quantity columns not found")
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
