from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

TOL = 1e-9


def _to_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    raise ValueError(f"Cannot parse date: {value!r}")


def _isin(t: dict) -> str:
    return str(t.get("isin") or t.get("security") or t.get("symbol") or "").strip().upper()

def _side(t: dict) -> str:
    return str(t.get("buy_sell") or t.get("side") or "").strip().upper()

def _qty(t: dict) -> float:
    q = t.get("quantity")
    if q is None:
        return 0.0
    try:
        return float(q)
    except (TypeError, ValueError):
        return 0.0

def _event_type(t: dict) -> str:
    return str(t.get("event_type") or t.get("type") or "trade").strip().lower()

def _date_field(t: dict, basis: str) -> Any:
    if basis == "settlement":
        return t.get("settlement_date") or t.get("trade_date") or t.get("date")
    return t.get("trade_date") or t.get("settlement_date") or t.get("date")

def _sort_key(t: dict, basis: str):
    d = _date_field(t, basis)
    try:
        dd = _to_date(d)
    except ValueError:
        dd = date.max
    return (dd, str(t.get("trade_no") or t.get("order_no") or t.get("id") or ""))

def _is_trade_event(t: dict) -> bool:
    et = _event_type(t)
    if et in ("cash", "external_cash", "cash_movement"):
        return False
    return et != "settlement"

def reconstruct(transactions, as_of_date, date_basis: str = "trade", opening_positions=None):
    cutoff = _to_date(as_of_date)
    tx_list = list(transactions or [])
    for op in (opening_positions or []):
        try:
            od = _to_date(op.get("ledger_opening_date") if isinstance(op, dict) else op.ledger_opening_date)
        except Exception:  # noqa: BLE001, S112
            continue
        if od <= cutoff:
            isin = op.get("security_id") if isinstance(op, dict) else op.security_id
            qty = op.get("opening_qty") if isinstance(op, dict) else op.opening_qty
            tx_list.append({"trade_date": od.isoformat(), "isin": isin, "buy_sell": "OPENING", "quantity": qty, "event_type": "opening"})
    transactions = tx_list
    relevant = []
    for t in transactions or []:
        if not _is_trade_event(t):
            continue
        d = _date_field(t, date_basis)
        if d is None:
            continue
        try:
            dd = _to_date(d)
        except ValueError:
            continue
        if dd <= cutoff:
            relevant.append(t)
    relevant.sort(key=lambda t: _sort_key(t, date_basis))

    quantities = defaultdict(float)
    for t in relevant:
        isin = _isin(t)
        if not isin:
            continue
        side = _side(t)
        q = _qty(t)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            quantities[isin] += q
        elif side == "SELL":
            quantities[isin] -= q

    negatives = {k: v for k, v in quantities.items() if v < -TOL}
    if negatives:
        raise ValueError(f"Invalid negative holdings: {negatives}")

    return {k: v for k, v in quantities.items() if abs(v) > TOL}

def get_holdings(transactions, as_of_date, date_basis: str = "trade", opening_positions=None):
    return reconstruct(transactions, as_of_date, date_basis, opening_positions=opening_positions)

def get_stock_quantity(transactions, security_id, as_of_date, date_basis: str = "trade", opening_positions=None) -> float:
    holdings = reconstruct(transactions, as_of_date, date_basis, opening_positions=opening_positions)
    return float(holdings.get(str(security_id).strip().upper(), 0.0))

def get_transaction_history(transactions, security_id) -> list:
    sid = str(security_id).strip().upper()
    out = [t for t in (transactions or []) if _isin(t) == sid]
    out.sort(key=lambda t: _sort_key(t, "trade"))
    return [dict(t) for t in out]

def get_symbol_aliases(transactions) -> dict:
    aliases = defaultdict(set)
    for t in transactions or []:
        isin = _isin(t)
        sym = t.get("symbol") or t.get("security")
        if isin and sym:
            aliases[isin].add(str(sym).strip())
    return {k: sorted(v) for k, v in aliases.items()}

def reconcile_holdings(transactions, snapshot_date, snapshot, date_basis: str = "trade") -> list:
    recon = reconstruct(transactions, snapshot_date, date_basis)
    snap_map = {}
    snap_symbol = {}
    if isinstance(snapshot, dict):
        for k, v in snapshot.items():
            snap_map[str(k).strip().upper()] = float(v or 0)
    else:
        for row in snapshot or []:
            isin = str(row.get("isin") or row.get("security") or "").strip().upper()
            if not isin:
                continue
            qv = row.get("quantity", row.get("qty"))
            snap_map[isin] = float(qv) if qv is not None else 0.0
            if row.get("symbol") or row.get("security"):
                snap_symbol[isin] = str(row.get("symbol") or row.get("security")).strip()
    all_isins = set(recon) | set(snap_map)
    out = []
    for isin in sorted(all_isins):
        r = recon.get(isin, 0.0)
        s = snap_map.get(isin, 0.0)
        if abs(r - s) > TOL:
            out.append({
                "isin": isin,
                "symbol": snap_symbol.get(isin),
                "reconstructed_qty": r,
                "snapshot_qty": s,
                "diff": r - s,
            })
    return out

def check_negative_holdings(transactions, as_of_date, date_basis: str = "trade") -> list:
    cutoff = _to_date(as_of_date)
    quantities = defaultdict(float)
    relevant = []
    for t in transactions or []:
        if not _is_trade_event(t):
            continue
        d = _date_field(t, date_basis)
        if d is None:
            continue
        try:
            dd = _to_date(d)
        except ValueError:
            continue
        if dd <= cutoff:
            relevant.append(t)
    relevant.sort(key=lambda t: _sort_key(t, date_basis))
    bad = []
    for t in relevant:
        isin = _isin(t)
        if not isin:
            continue
        side = _side(t)
        q = _qty(t)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            quantities[isin] += q
        elif side == "SELL":
            quantities[isin] -= q
        if quantities[isin] < -TOL:
            bad.append(isin)
    return sorted(set(bad))


class TransactionLedger:
    def __init__(self, transactions=None):
        self.transactions = [dict(t) for t in (transactions or [])]

    def add(self, transaction: dict) -> None:
        self.transactions.append(dict(transaction))

    def get_holdings(self, as_of_date, date_basis: str = "trade"):
        return get_holdings(self.transactions, as_of_date, date_basis)

    def get_stock_quantity(self, security_id, as_of_date, date_basis: str = "trade", opening_positions=None) -> float:
        return get_stock_quantity(self.transactions, security_id, as_of_date, date_basis, opening_positions=opening_positions)

    def get_transaction_history(self, security_id) -> list:
        return get_transaction_history(self.transactions, security_id)

    def reconcile_holdings(self, snapshot_date, snapshot, date_basis: str = "trade") -> list:
        return reconcile_holdings(self.transactions, snapshot_date, snapshot, date_basis)

    def get_symbol_aliases(self) -> dict:
        return get_symbol_aliases(self.transactions)

    def check_negative_holdings(self, as_of_date, date_basis: str = "trade") -> list:
        return check_negative_holdings(self.transactions, as_of_date, date_basis)

