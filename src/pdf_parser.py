"""Parse ICICI Direct equity transaction statement PDFs."""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pdfplumber

SETTLEMENT_ALIASES = {"settlementdate", "settlement date", "sett date", "settdate"}
ISIN_ALIASES = {"isin", "isincode", "isin number"}
SECURITY_ALIASES = {"security", "securityname", "scripname", "scrip", "name"}
BUYZELL_ALIASES = {"buy/sell", "buysell", "b/s", "type", "side"}
QUANTITY_ALIASES = {"quantity", "qty", "units"}
NET_AMOUNT_ALIASES = {"netamount", "net amount", "netamount(`)", "net amount (`)"}


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _find_col(header: list, aliases: set) -> int | None:
    for i, h in enumerate(header):
        n = _norm(h)
        if not n:
            continue
        if n in aliases:
            return i
        for a in aliases:
            if a and (a in n or n in a):
                return i
    return None


def _parse_date(value: Any) -> str | None:
    s = str(value or "").strip().replace("\n", " ")
    if not s:
        return None
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", s)
    if m:
        d, mth, y = m.groups()
        return f"{y}-{mth}-{d}"
    return None


def _is_header(row: list) -> bool:
    joined = " ".join(str(c or "") for c in row).lower()
    return "isin" in joined and ("buy" in joined or "sell" in joined or "quantity" in joined)


def parse_pdf(path: str) -> list[dict]:
    rows_out: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                if not table:
                    continue
                header = None
                for r in table:
                    if _is_header(r):
                        header = r
                        break
                if header is None:
                    header = table[0]
                    data_rows = table[1:]
                else:
                    data_rows = [r for r in table if r is not header]
                si = _find_col(header, SETTLEMENT_ALIASES)
                ii = _find_col(header, ISIN_ALIASES)
                if ii is None:
                    continue
                sei = _find_col(header, SECURITY_ALIASES)
                bi = _find_col(header, BUYZELL_ALIASES)
                qi = _find_col(header, QUANTITY_ALIASES)
                ni = _find_col(header, NET_AMOUNT_ALIASES)
                for r in data_rows:
                    if not r or all((c is None or str(c).strip() == "") for c in r):
                        continue
                    joined = " ".join(str(c or "") for c in r).lower()
                    if "total" in joined and (ii is None or not str(r[ii] or "").strip()):
                        continue
                    isin = str(r[ii]).strip().upper() if ii is not None and r[ii] is not None else ""
                    if not isin or not re.match(r"^[A-Z]{2}[A-Z0-9]{9}\d$", isin):
                        continue
                    bs_raw = str(r[bi]).strip().upper() if bi is not None and r[bi] is not None else ""
                    if bs_raw in ("B", "BUY", "B/S"):
                        bs = "BUY"
                    elif bs_raw in ("S", "SELL"):
                        bs = "SELL"
                    else:
                        bs = bs_raw
                    try:
                        qty = float(str(r[qi]).replace(",", "").strip()) if qi is not None and r[qi] is not None else None
                    except ValueError:
                        qty = None
                    try:
                        net = float(str(r[ni]).replace(",", "").strip()) if ni is not None and r[ni] is not None else None
                    except ValueError:
                        net = None
                    sec = str(r[sei]).strip().replace("\n", " ") if sei is not None and r[sei] is not None else ""
                    sett = _parse_date(r[si]) if si is not None else None
                    rows_out.append({
                        "settlement_date": sett,
                        "isin": isin,
                        "security": sec,
                        "buy_sell": bs,
                        "quantity": qty,
                        "net_amount": net,
                        "source_file": Path(path).name,
                    })
    return rows_out


def discover_pdfs(folder: str) -> list[str]:
    folder = Path(folder)
    if not folder.exists():
        return []
    files = [str(p) for p in folder.glob("*.pdf")]
    def sort_key(p):
        name = Path(p).name
        m = re.search(r"(\d{2})[-_](\d{2})[-_](\d{4})", name)
        if m:
            d, mo, y = m.groups()
            try:
                return (0, datetime(int(y), int(mo), int(d)).timestamp())
            except ValueError:
                pass
        try:
            return (1, os.path.getmtime(p))
        except OSError:
            return (2, 0)
    files.sort(key=sort_key)
    return files


def parse_folder(folder: str) -> list[dict]:
    all_rows: list[dict] = []
    for pdf in discover_pdfs(folder):
        try:
            all_rows.extend(parse_pdf(pdf))
        except Exception:  # noqa: BLE001
            continue
    all_rows.sort(key=lambda r: (r.get("settlement_date") or "9999", r.get("isin") or ""))
    return all_rows
