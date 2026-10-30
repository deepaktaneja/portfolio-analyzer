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




# ... keep imports; add store import lazily to avoid circular
def parse_folder(folder: str, store=None) -> list[dict]:
    all_rows: list[dict] = []
    known = store.known_files() if store is not None else {}
    for pdf in discover_pdfs(folder):
        name = Path(pdf).name
        try:
            mtime = os.path.getmtime(pdf)
        except OSError:
            mtime = 0.0
        if store is not None and name in known and abs(known[name] - mtime) < 1e-9:
            # already parsed; skip
            continue
        try:
            rows = parse_pdf(pdf)
        except Exception:  # noqa: BLE001, S112
            continue
        if store is not None:
            store.replace_file_transactions(name, rows)
            store.upsert_file(name, mtime, len(rows))
        all_rows.extend(rows)
    if store is not None:
        # Remove stale entries for PDFs that no longer exist in the folder.
        present = {Path(pdf).name for pdf in discover_pdfs(folder)}
        for stale in list(known):
            if stale not in present:
                store.remove_file(stale)
        return store.all_transactions()
    # fallback: re-parse everything (no store)
    if not all_rows:
        for pdf in discover_pdfs(folder):
            try:
                all_rows.extend(parse_pdf(pdf))
            except Exception:  # noqa: BLE001, S112
                continue
    all_rows.sort(key=lambda r: (r.get("settlement_date") or "9999", r.get("isin") or ""))
    return all_rows


def _quarter_label(date_str: str) -> str:
    y, m = date_str.split("-")[:2]
    q = (int(m) - 1) // 3 + 1
    return f"{y} Q{q}"


def _quarter_key(label: str) -> tuple[int, int]:
    y, q = label.split()
    return (int(y), int(q[1:]))


def detect_missing_quarters(transactions: list[dict]) -> list[str]:
    """Return quarter labels between min/max transaction quarter that have no rows."""
    present: set[str] = set()
    for t in transactions or []:
        d = t.get("settlement_date")
        if d:
            present.add(_quarter_label(str(d)))
    if not present:
        return []
    min_label = min(present, key=_quarter_key)
    max_label = max(present, key=_quarter_key)
    expected: list[str] = []
    y, q = _quarter_key(min_label)
    end_y, end_q = _quarter_key(max_label)
    while (y, q) <= (end_y, end_q):
        expected.append(f"{y} Q{q}")
        q += 1
        if q > 4:
            q = 1
            y += 1
    return sorted([q for q in expected if q not in present], key=_quarter_key)


def detect_duplicate_details(transactions: list[dict]) -> list[dict]:
    """Detect files whose ISIN/security date ranges overlap with earlier files."""
    file_keys: dict[str, dict[str, dict]] = {}
    for t in transactions or []:
        f = t.get("source_file")
        key = t.get("isin") or t.get("security")
        d = t.get("settlement_date")
        if not f or not key or not d:
            continue
        d = str(d)
        bucket = file_keys.setdefault(f, {}).setdefault(
            key, {"min": d, "max": d, "dates": {d}}
        )
        bucket["min"] = min(bucket["min"], d)
        bucket["max"] = max(bucket["max"], d)
        bucket["dates"].add(d)

    files = sorted(file_keys)
    details: list[dict] = []
    for i, f in enumerate(files):
        conflicts: set[str] = set()
        keys: set[str] = set()
        for key, info in file_keys[f].items():
            for other in files[:i]:
                other_info = file_keys[other].get(key)
                if not other_info:
                    continue
                if info["min"] <= other_info["max"] and other_info["min"] <= info["max"] or info["dates"] & other_info["dates"]:
                    conflicts.add(other)
                    keys.add(key)
        if conflicts:
            details.append(
                {
                    "ignored_file": f,
                    "conflicts_with": sorted(conflicts),
                    "keys": sorted(keys),
                }
            )
    details.sort(key=lambda d: d["ignored_file"])
    return details


def detect_duplicate_files(transactions: list[dict]) -> list[str]:
    return sorted({d["ignored_file"] for d in detect_duplicate_details(transactions)})


def filter_transactions_with_buy_details(transactions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep sells only when a matching earlier buy exists for the same ISIN."""
    buys_by_isin: dict[str, list[str]] = {}
    for t in transactions or []:
        if t.get("buy_sell") == "BUY" and t.get("isin") and t.get("settlement_date"):
            buys_by_isin.setdefault(t["isin"], []).append(str(t["settlement_date"]))

    filtered: list[dict] = []
    ignored: list[dict] = []
    for t in transactions or []:
        if t.get("buy_sell") != "SELL":
            filtered.append(t)
            continue
        isin = t.get("isin")
        sell_date = t.get("settlement_date")
        buy_dates = buys_by_isin.get(isin, [])
        if sell_date and any(bd <= str(sell_date) for bd in buy_dates):
            filtered.append(t)
        else:
            ignored.append(t)
    return filtered, ignored


# Backwards/alternative names for the statement folder helpers.
discover_statements = discover_pdfs
discover_statement_pdfs = discover_pdfs
discover_statement_files = discover_pdfs
parse_statements = parse_folder
parse_statement_folder = parse_folder

# Validation aliases
missing_quarters = detect_missing_quarters
duplicate_files = detect_duplicate_files
duplicate_details = detect_duplicate_details
filter_buy_details = filter_transactions_with_buy_details
