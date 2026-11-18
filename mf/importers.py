"""MF statement importer: CSV, Excel, CAS statements."""
from __future__ import annotations

from datetime import datetime

SIDE_ALIASES = {
    "sip": "SIP",
    "purchase": "PURCHASE",
    "lump_sum": "PURCHASE",
    "redeem": "REDEMPTION",
    "redemption": "REDEMPTION",
    "withdraw": "REDEMPTION",
    "switch_in": "SWITCH_IN",
    "switch_out": "SWITCH_OUT",
    "stp": "STP",
    "swp": "SWP",
    "idcw": "IDCW",
    "dividend": "IDCW",
    "reinvest": "PURCHASE",
    "buy": "PURCHASE",
    "sell": "REDEMPTION",
    "exit": "REDEMPTION",
    "invest": "PURCHASE",
    "investment": "PURCHASE",
    "subscription": "PURCHASE",
    "allotment": "PURCHASE",
    "bonus": "PURCHASE",
    "growth": "PURCHASE",
    "accumulation": "PURCHASE",
}

# Column aliases for MF Central / AMC / CAS statement tables.
# NOTE: "type" is intentionally NOT a side alias (AMC statements have a separate
# "Type" = Equity/Bond column); "unit_balance"/"nav_date" are NOT trade fields so
# a current-valuation statement is never mistaken for a transaction statement.
MF_COLUMN_ALIASES = {
    "scheme_code": {"schemecode", "scheme_code", "amfi_code", "amficode", "code", "productcode", "product_code", "scheme"},
    "scheme_name": {"schemname", "scheme_name", "schemename", "fund", "fund_name", "schemedescription", "description", "name"},
    "trade_date": {"date", "transactiondate", "trade_date", "tradedate", "purchasedate", "dateoftransaction", "date_of_transaction"},
    "side": {"transactiontype", "transaction_type", "side", "transaction", "activity"},
    "units": {"units", "unit", "quantity", "qty", "allottedunits", "allotted_units"},
    "nav": {"nav", "price", "nav_price", "unit_price", "rate"},
    "amount": {"amount", "net_amount", "value", "transaction_amount", "gross_amount"},
}


def _norm_col(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _detect_mf_columns(df) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set = set()
    for field, aliases in MF_COLUMN_ALIASES.items():
        for col in df.columns:
            if col in used:
                continue
            if _norm_col(col) in {_norm_col(a) for a in aliases}:
                mapping[field] = col
                used.add(col)
                break
    return mapping


def detect_mf_statement(path) -> bool:
    """Heuristically detect whether a file is an MF statement (by name or columns)."""
    import os
    name = os.path.basename(str(path)).lower()
    if any(k in name for k in ("mf", "mutual", "cas", "mfcentral", "nav")):
        return True
    try:
        import pandas as pd
        if str(path).lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(path)
        else:
            df = pd.read_csv(path, sep=None, engine="python")
        cols = _detect_mf_columns(df)
        has_qty = "units" in cols or "nav" in cols
        has_key = "trade_date" in cols and ("scheme_code" in cols or "scheme_name" in cols)
        return bool(has_qty and has_key)
    except Exception:  # noqa: BLE001
        return False


def is_mf_statement(path) -> bool:
    """Backwards-compatible alias for :func:`detect_mf_statement`."""
    return detect_mf_statement(path)


_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y",
                 "%d %b %Y", "%d-%B-%Y", "%Y/%m/%d", "%d.%m.%Y")


def _to_float(v):
    import math
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return None if math.isnan(v) else float(v)
        except (TypeError, ValueError):
            return None
    s = str(v).strip().replace(",", "")
    if s in ("", "nan", "none", "-", "nat", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_iso(v):
    """Normalise assorted date strings (incl. ``27-SEP-2006``) to ISO YYYY-MM-DD."""
    if v is None:
        return None
    if hasattr(v, "date") and not isinstance(v, str):
        try:
            return v.date().isoformat()
        except Exception:  # noqa: BLE001, S110
            pass
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s[:20], fmt).date().isoformat()
        except ValueError:
            continue
    try:
        import pandas as pd

        return pd.Timestamp(v).date().isoformat()
    except Exception:  # noqa: BLE001
        return s[:10] if len(s) >= 10 else None


def _read_raw(path):
    import pandas as pd
    if str(path).lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path, header=None)
    last = None
    for sep in (None, ",", "\t", ";"):
        try:
            raw = pd.read_csv(path, header=None, sep=sep, engine="python")
            if raw.shape[1] > 1:
                return raw
        except Exception as e:  # noqa: BLE001
            last = e
    if last is not None:
        raise last
    return pd.DataFrame()


def _load_table(path):
    """Read a statement into a DataFrame, auto-detecting the header row."""
    import pandas as pd
    raw = _read_raw(path)
    if raw is None or raw.empty:
        return pd.DataFrame()
    markers = ("tradedate", "transactiontype", "unitbalance", "schemecode", "units", "date", "navdate")
    best, best_score = 0, -1
    for i in range(min(6, len(raw))):
        norm = [_norm_col(str(x)) for x in raw.iloc[i].tolist()]
        nonempty = sum(1 for c in norm if c)
        score = nonempty + (100 if any(m in norm for m in markers) else 0)
        if score > best_score:
            best_score, best = score, i
    header = [str(x).strip() for x in raw.iloc[best].tolist()]
    df = raw.iloc[best + 1:].copy()
    seen: dict[str, int] = {}
    cols = []
    for j, h in enumerate(header):
        name = h if h and h.lower() not in ("nan", "none") else f"col{j}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        cols.append(name)
    df.columns = cols
    return df.reset_index(drop=True)


def _norm_side(raw):
    """Map an AMC transaction-type string to a normalised side, or None (non-txn)."""
    if raw is None:
        return None
    s = _norm_col(raw)
    if not s:
        return None
    real = any(k in s for k in ("switch", "redempt", "redeem", "purchase", "sip", "stp",
                                "swp", "idcw", "dividend", "payout", "invest", "allotment"))
    admin = any(k in s for k in ("nominee", "registered", "registration", "cancel", "address",
                                 "kra", "mandate", "panchange", "consolidation", "terminated",
                                 "correction", "contact", "nilbalance", "changeof"))
    if admin and not real:
        return None
    if "switch" in s and "out" in s:
        return "SWITCH_OUT"
    if "switch" in s and "in" in s:
        return "SWITCH_IN"
    if "stp" in s and "out" in s:
        return "STP_OUT"
    if "stp" in s and "in" in s:
        return "STP_IN"
    if any(k in s for k in ("redempt", "redeem", "withdraw", "swp", "extinguish")):
        return "REDEMPTION"
    if any(k in s for k in ("idcw", "dividend", "payout")):
        return "IDCW"
    if "switch" in s:
        return "SWITCH_IN"
    if "sip" in s:
        return "SIP"
    if any(k in s for k in ("purchase", "invest", "allotment", "subscription", "bonus",
                            "reinvest", "growth", "accumulation", "creation", "systematicpurchase")):
        return "PURCHASE"
    return SIDE_ALIASES.get(s)


VAL_COLUMN_ALIASES = {
    "scheme_code": {"schemecode", "scheme_code", "code", "productcode", "product_code"},
    "scheme_name": {"scheme", "schemename", "scheme_name", "fund", "fund_name", "description"},
    "folio": {"folio", "folionumber", "folio_number"},
    "units": {"unitbalance", "unit_balance", "units", "unit", "balance"},
    "nav_date": {"navdate", "nav_date", "date", "valuationdate", "valuation_date"},
    "current_value": {"currentvaluers", "currentvalue", "current_value", "marketvalue", "value"},
    "cost_value": {"costvaluers", "costvalue", "cost_value", "cost", "investedvalue"},
}


def _detect_val_columns(df) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set = set()
    for field, aliases in VAL_COLUMN_ALIASES.items():
        for col in df.columns:
            if col in used:
                continue
            if _norm_col(col) in {_norm_col(a) for a in aliases}:
                mapping[field] = col
                used.add(col)
                break
    return mapping


def parse_mf_statement(path) -> tuple[list[dict], list[str]]:
    """Parse an MF Central / AMC statement (CSV/XLSX/XLS) into normalised transactions.

    Returns (rows, warnings). Each row: scheme_code, scheme_name, trade_date (ISO),
    side, units, nav, amount. Administrative/non-unit rows (nominee registration,
    address updates, cancellations, etc.) are skipped.
    """
    warnings: list[str] = []
    try:
        df = _load_table(path)
    except Exception as e:  # noqa: BLE001
        return [], [f"Could not read MF statement: {path}: {e}"]
    if df is None or getattr(df, "empty", True):
        return [], [f"Could not read MF statement: {path}"]

    cols = _detect_mf_columns(df)
    if "trade_date" not in cols or "units" not in cols:
        return [], ["MF statement missing required date/units columns"]

    rows: list[dict] = []
    for i, r in df.iterrows():
        try:
            trade_date = _to_iso(r[cols["trade_date"]])
            if not trade_date:
                continue
            side = _norm_side(r[cols["side"]]) if "side" in cols else "PURCHASE"
            if side is None:
                continue  # recognised non-transaction event
            units = _to_float(r[cols["units"]])
            nav = _to_float(r[cols["nav"]]) if "nav" in cols else None
            amount = _to_float(r[cols["amount"]]) if "amount" in cols else None
            if amount is None:
                amount = (units or 0.0) * nav if nav else None
            units_abs = abs(units) if units is not None else 0.0
            # Keep IDCW/dividend cash payouts even when units don't change.
            if units_abs == 0 and not (side == "IDCW" and amount and amount > 0):
                continue  # non-unit / administrative row
            if amount is None:
                warnings.append(f"row {i}: missing amount/nav, skipped")
                continue
            scheme_code = None
            if "scheme_code" in cols:
                v = r[cols["scheme_code"]]
                if v is not None and str(v).strip().lower() not in ("", "nan", "none"):
                    scheme_code = str(v).strip()
            scheme_name = None
            if "scheme_name" in cols:
                v = r[cols["scheme_name"]]
                if v is not None and str(v).strip().lower() not in ("", "nan", "none"):
                    scheme_name = str(v).strip()
            rows.append({
                "scheme_code": scheme_code or scheme_name,
                "scheme_name": scheme_name,
                "trade_date": trade_date,
                "side": side,
                "units": units_abs,
                "nav": nav,
                "amount": abs(amount),
            })
        except Exception as e:  # noqa: BLE001
            warnings.append(f"row {i}: {e}")
    return rows, warnings


def parse_current_valuation(path) -> list[dict]:
    """Parse an AMC 'current valuation' statement into per-scheme holding rows.

    Each row: scheme_code, scheme_name, folio, units, nav (current/units),
    nav_date (ISO), current_value, cost_value. Rows with no units are skipped.
    """
    try:
        df = _load_table(path)
    except Exception:  # noqa: BLE001
        return []
    if df is None or getattr(df, "empty", True):
        return []
    cols = _detect_val_columns(df)
    if "scheme_code" not in cols or "units" not in cols:
        return []
    out: list[dict] = []
    for _, r in df.iterrows():
        code = r.get(cols["scheme_code"])
        code = "" if code is None else str(code).strip()
        units = _to_float(r.get(cols["units"])) if "units" in cols else None
        if not code or code.lower() in ("nan", "none") or units is None or units <= 0:
            continue
        cur = _to_float(r.get(cols["current_value"])) if "current_value" in cols else None
        cost = _to_float(r.get(cols["cost_value"])) if "cost_value" in cols else None
        navd = _to_iso(r.get(cols["nav_date"])) if "nav_date" in cols else None
        name = r.get(cols["scheme_name"]) if "scheme_name" in cols else None
        folio = r.get(cols["folio"]) if "folio" in cols else None
        nav = (cur / units) if (cur and units > 0) else None
        out.append({
            "scheme_code": code,
            "scheme_name": "" if name is None else str(name).strip(),
            "folio": "" if folio is None else str(folio).strip(),
            "units": units,
            "nav": nav,
            "nav_date": navd,
            "current_value": cur,
            "cost_value": cost,
        })
    return out
