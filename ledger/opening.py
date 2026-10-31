from __future__ import annotations

from datetime import date, datetime

TOL = 1e-9

def _to_date(v):
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                return datetime.strptime(v.strip(), fmt).date()
            except ValueError:
                continue
    raise ValueError("Cannot parse date")



def _isin(t):
    return str(t.get("isin") or t.get("security") or t.get("symbol") or "").strip().upper()

def _side(t):
    return str(t.get("buy_sell") or t.get("side") or "").strip().upper()

def _qty(t):
    q = t.get("quantity")
    try:
        return float(q) if q is not None else 0.0
    except (TypeError, ValueError):
        return 0.0

def _event_type(t):
    return str(t.get("event_type") or t.get("type") or "trade").strip().lower()

def _is_trade(t):
    et = _event_type(t)
    if et in ("cash", "external_cash", "cash_movement", "settlement"):
        return False
    return _side(t) in ("BUY", "SELL", "OPENING", "OPEN", "OPENING_POSITION")

def _date_field(t, basis="trade"):
    if basis == "settlement":
        return t.get("settlement_date") or t.get("trade_date") or t.get("date")
    return t.get("trade_date") or t.get("settlement_date") or t.get("date")

def infer_minimum_opening(transactions, actions=None, as_of_date=None):
    trades = [t for t in (transactions or []) if _is_trade(t)]
    trades.sort(key=lambda t: (_to_date(_date_field(t)), str(t.get("trade_no") or t.get("order_no") or "")))
    running = {}
    min_running = {}
    for t in trades:
        isin = _isin(t)
        if not isin:
            continue
        d = _to_date(_date_field(t))
        if as_of_date is not None and d > _to_date(as_of_date):
            continue
        side = _side(t)
        q = _qty(t)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            running[isin] = running.get(isin, 0.0) + q
        elif side == "SELL":
            running[isin] = running.get(isin, 0.0) - q
        if isin not in min_running or running[isin] < min_running[isin]:
            min_running[isin] = running[isin]
    result = {}
    for isin, mn in min_running.items():
        if mn < -1e-9:
            result[isin] = -mn
    return result

def categorize_issue(security_id, transactions):
    txs = [t for t in (transactions or []) if _isin(t) == security_id]
    has_sell = any(_side(t) == "SELL" for t in txs)
    has_buy = any(_side(t) in ("BUY", "OPENING") for t in txs)
    if has_sell and not has_buy:
        return "missing_opening"
    if has_sell and has_buy:
        buys = sum(_qty(t) for t in txs if _side(t) in ("BUY", "OPENING"))
        sells = sum(_qty(t) for t in txs if _side(t) == "SELL")
        if sells > buys:
            return "missing_opening"
    return "invalid_transaction"

def reconcile_opening_with_snapshot(transactions, actions, snapshot_holdings, snapshot_date):
    net = {}
    for t in transactions or []:
        d = _date_field(t)
        if d is None:
            continue
        try:
            dd = _to_date(d)
        except ValueError:
            continue
        if dd > _to_date(snapshot_date):
            continue
        isin = _isin(t)
        if not isin:
            continue
        q = _qty(t)
        if _side(t) in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            net[isin] = net.get(isin, 0.0) + q
        elif _side(t) == "SELL":
            net[isin] = net.get(isin, 0.0) - q
    snap = {}
    if isinstance(snapshot_holdings, dict):
        for k, v in snapshot_holdings.items():
            snap[str(k).strip().upper()] = float(v or 0)
    else:
        for row in snapshot_holdings or []:
            isin = str(row.get("isin") or row.get("security") or "").strip().upper()
            if isin:
                snap[isin] = float(row.get("quantity") or row.get("qty") or 0)
    result = {}
    for isin in set(net) | set(snap):
        current = snap.get(isin, 0.0)
        change = net.get(isin, 0.0)
        opening = current - change
        if opening > 1e-9:
            result[isin] = opening
    return result
