"""ICICI Direct equity transaction PDF importer (Stage 01)."""
from __future__ import annotations

import re
from typing import Any

import pdfplumber

from importers.base import NormalizedTransaction, RawRow, fingerprint_transaction
from importers.detection import detect_pdf

SETTLEMENT_ALIASES = {"settlementdate", "settlement date", "sett date", "settdate"}
ISIN_ALIASES = {"isin", "isincode", "isin number"}
SECURITY_ALIASES = {"security", "securityname", "scripname", "scrip", "name"}
BUYSELL_ALIASES = {"buy/sell", "buysell", "b/s", "type", "side"}
QUANTITY_ALIASES = {"quantity", "qty", "units"}
RATE_ALIASES = {"grossrate", "gross rate", "rate", "price", "persecurity"}
GROSS_ALIASES = {"total", "grossamount", "gross amount", "total(`)", "total (rs)"}
BROKERAGE_ALIASES = {"brokerage", "brokerage(total)", "brokerage (total)"}
GST_ALIASES = {"goods&servicetax", "goods and service tax", "gst", "service tax"}
NET_ALIASES = {"netamount", "net amount", "netamount(`)", "net amount (`)"}
TRADE_DATE_ALIASES = {"tradedate", "trade date", "tradedatetime"}
ORDER_ALIASES = {"orderno", "order no", "order number", "contractnumber", "contract number"}
TRADE_NO_ALIASES = {"tradeno", "trade no", "trade number"}
EXCHANGE_ALIASES = {"exchange", "exchangecode", "exchange code"}


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _find_col(header: list, aliases: set) -> int | None:
    for i, h in enumerate(header):
        n = _norm(h)
        if not n:
            continue
        if n in {_norm(a) for a in aliases}:
            return i
        for a in aliases:
            an = _norm(a)
            if an and (an in n or n in an):
                return i
    return None


def _parse_date(value: Any) -> str | None:
    s = str(value or "").strip().replace("\n", " ")
    if not s:
        return None
    import datetime as _dt

    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d-%b-%y"):
        try:
            return _dt.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{mo}-{d}"
    m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{2})", s)
    if m:
        d, mo, y = m.groups()
        return f"20{y}-{mo}-{d}"
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).replace(",", "").replace("`", "").strip()
    if not s or s.lower() in ("null", "none", "nan", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _is_header(row: list) -> bool:
    joined = " ".join(str(c or "") for c in row).lower()
    return "isin" in joined and ("buy" in joined or "sell" in joined or "quantity" in joined)
_COLUMN_MAPS: dict = {}


class IciciEquityTransactionPdfParser:
    name = "icici_equity_transaction_pdf"

    def detect(self, doc) -> bool:

        return detect_pdf(doc.path) == "pdf_text"

    def parse(self, doc) -> list:

        rows = []
        with pdfplumber.open(doc.path) as pdf:
            for pi, page in enumerate(pdf.pages, start=1):
                for ti, table in enumerate(page.extract_tables() or []):
                    if not table:
                        continue
                    header = None
                    for r in table:
                        if _is_header(r):
                            header = r
                            break
                    if header is None:
                        header = table[0]
                    colmap = {
                        "isin": _find_col(header, ISIN_ALIASES),
                        "security": _find_col(header, SECURITY_ALIASES),
                        "buy_sell": _find_col(header, BUYSELL_ALIASES),
                        "quantity": _find_col(header, QUANTITY_ALIASES),
                        "gross_rate": _find_col(header, RATE_ALIASES),
                        "gross_amount": _find_col(header, GROSS_ALIASES),
                        "brokerage": _find_col(header, BROKERAGE_ALIASES),
                        "gst": _find_col(header, GST_ALIASES),
                        "net_amount": _find_col(header, NET_ALIASES),
                        "trade_date": _find_col(header, TRADE_DATE_ALIASES),
                        "settlement": _find_col(header, SETTLEMENT_ALIASES),
                        "exchange": _find_col(header, EXCHANGE_ALIASES),
                        "order": _find_col(header, ORDER_ALIASES),
                        "trade_no": _find_col(header, TRADE_NO_ALIASES),
                    }
                    colmap = {k: v for k, v in colmap.items() if v is not None}
                    _COLUMN_MAPS[(doc.name, pi, ti)] = colmap
                    for ri, r in enumerate(table):
                        if r is None or all((c is None or str(c).strip() == "") for c in r):
                            continue
                        if _is_header(r):
                            continue
                        joined = " ".join(str(c or "") for c in r).lower()
                        isin_idx = colmap.get("isin")
                        if isin_idx is not None and isin_idx < len(r):
                            isin_val = str(r[isin_idx] or "").strip().upper()
                            if not isin_val or not re.match(r"^[A-Z]{2}[A-Z0-9]{9}\d$", isin_val):
                                continue
                        if "total" in joined and (isin_idx is None or not str(r[isin_idx] or "").strip()):
                            continue
                        rows.append(
                            RawRow(source_file=doc.name, page_number=pi, table_index=ti, row_index=ri, cells=list(r))


                        )
        return rows
    def normalize(self, raw) -> list:

        cells = raw.cells
        colmap = _COLUMN_MAPS.get((raw.source_file, raw.page_number, raw.table_index))
        if not colmap:
            return []
        isin_i = colmap.get("isin")
        if isin_i is None or isin_i >= len(cells):
            return []
        isin = str(cells[isin_i] or "").strip().upper()
        if not isin or not re.match(r"^[A-Z]{2}[A-Z0-9]{9}\d$", isin):
            return []

        def get(key):
            i = colmap.get(key)
            if i is None or i >= len(cells):
                return None
            return cells[i]

        bs_raw = str(get("buy_sell") or "").strip().upper()
        if bs_raw in ("B", "BUY", "B/S"):
            side = "BUY"
        elif bs_raw in ("S", "SELL"):
            side = "SELL"
        else:
            side = bs_raw or None

        qty = _to_float(get("quantity"))
        rate = _to_float(get("gross_rate"))
        gross = _to_float(get("gross_amount"))
        broker = _to_float(get("brokerage"))
        gst = _to_float(get("gst"))
        net = _to_float(get("net_amount"))
        charges = None
        if broker is not None or gst is not None:
            charges = (broker or 0.0) + (gst or 0.0)
        if gross is None and qty is not None and rate is not None:
            gross = qty * rate
        if net is None and gross is not None:
            net = gross
            if charges is not None:
                net = gross - charges if side == "SELL" else gross + charges

        trade_date = _parse_date(get("trade_date"))
        sett_date = _parse_date(get("settlement"))
        security = str(get("security") or "").strip().replace("\n", " ") or None
        exchange = str(get("exchange") or "").strip() or None
        order_no = str(get("order") or "").strip().replace("\n", " ") or None
        trade_no = str(get("trade_no") or "").strip() or None

        issues = []
        if not sett_date and not trade_date:
            issues.append("missing_date")
        if qty is None:
            issues.append("missing_quantity")
        if net is None:
            issues.append("missing_net_amount")

        tx = NormalizedTransaction(
            source_file=raw.source_file,
            page_number=raw.page_number,
            row_index=raw.row_index,
            trade_date=trade_date,
            settlement_date=sett_date,
            exchange=exchange,
            order_no=order_no,
            trade_no=trade_no,
            isin=isin,
            security=security,
            side=side,
            quantity=qty,
            gross_rate=rate,
            gross_amount=gross,
            brokerage=broker,
            gst=gst,
            charges=charges,
            net_amount=net,
            confidence=1.0 if not issues else 0.5,
            issues=issues,
            raw_cells=list(raw.cells),
        )
        tx.fingerprint = fingerprint_transaction(tx)
        return [tx]

    def parse_document(self, path: str):
        from importers.detection import detect_source

        doc = detect_source(path)
        raws = self.parse(doc)
        txs = []
        for r in raws:
            txs.extend(self.normalize(r))
        return raws, txs


def parse_document(path: str):
    from importers.detection import detect_source

    parser = IciciEquityTransactionPdfParser()
    doc = detect_source(path)
    raws = parser.parse(doc)
    txs = []
    for r in raws:
        txs.extend(parser.normalize(r))
    return raws, txs
