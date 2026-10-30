"""FIFO cost-basis engine (Stage 06)."""
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
    raise ValueError(f"Cannot parse date: {v!r}")

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

def _cost(t):
    n = t.get("net_amount")
    if n is not None:
        try:
            return float(n)
        except (TypeError, ValueError):
            pass
    q = _qty(t)
    p = t.get("avg_price") or t.get("gross_rate")
    if p is not None:
        try:
            return q * float(p)
        except (TypeError, ValueError):
            pass
    return 0.0

def _trade_date(t):
    return t.get("trade_date") or t.get("settlement_date") or t.get("date")

def _is_trade(t):
    et = str(t.get("event_type") or t.get("type") or "trade").strip().lower()
    if et in ("cash", "external_cash", "cash_movement", "settlement"):
        return False
    return _side(t) in ("BUY", "SELL", "OPENING", "OPEN", "OPENING_POSITION")

def _action_date(a):
    return a.effective_date or a.record_date or a.ex_date


def _build_events(transactions, actions):
    events = []
    for t in transactions or []:
        if not _is_trade(t):
            continue
        events.append(("trade", t, _to_date(_trade_date(t))))
    for a in actions or []:
        d = _action_date(a)
        if d is None:
            continue
        events.append(("action", a, _to_date(d)))
    events.sort(key=lambda e: (e[2], 0 if e[0] == "trade" else 1))
    return events

def get_fifo_lots(transactions, actions=None, security_id=None, as_of_date=None):
    cutoff = _to_date(as_of_date) if as_of_date is not None else None
    by_isin = {}
    review = []
    for kind, obj, d in _build_events(transactions, actions):
        if cutoff is not None and d > cutoff:
            break
        if kind == "trade":
            t = obj
            isin = _isin(t)
            if not isin:
                continue
            if security_id is not None and isin != str(security_id).strip().upper():
                continue
            side = _side(t)
            q = _qty(t)
            if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
                by_isin.setdefault(isin, []).append({"isin": isin, "qty": q, "cost": _cost(t), "date": _to_date(_trade_date(t)), "source": "trade"})
            elif side == "SELL":
                remaining = q
                for lot in by_isin.setdefault(isin, []):
                    if remaining <= TOL:
                        break
                    if lot["qty"] <= TOL:
                        continue
                    take = min(lot["qty"], remaining)
                    ratio = take / lot["qty"] if lot["qty"] else 0.0
                    lot["qty"] -= take
                    lot["cost"] -= lot["cost"] * ratio
                    remaining -= take
        else:
            a = obj
            sid = a.security_id.strip().upper()
            if security_id is not None and sid != str(security_id).strip().upper() and a.successor_id != str(security_id).strip().upper():
                continue
            from corporate_actions.engine import _apply_action
            lots_flat = [l for ls in by_isin.values() for l in ls]
            _apply_action(lots_flat, a, review)
    lots = []
    for isin, ls in by_isin.items():
        if security_id is not None and isin != str(security_id).strip().upper():
            continue
        for lot in ls:
            if lot["qty"] > TOL:
                lots.append({"isin": lot["isin"], "qty": lot["qty"], "cost": lot["cost"], "date": lot["date"], "source": lot.get("source", "trade")})
    return lots


def calculate_realized_gains(transactions, actions=None, start_date=None, end_date=None, price_lookup=None):
    sd = _to_date(start_date) if start_date is not None else None
    ed = _to_date(end_date) if end_date is not None else None
    by_isin = {}
    review = []
    gains = []
    for kind, obj, d in _build_events(transactions, actions):
        if kind == "trade":
            t = obj
            isin = _isin(t)
            if not isin:
                continue
            side = _side(t)
            q = _qty(t)
            if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
                by_isin.setdefault(isin, []).append({"isin": isin, "qty": q, "cost": _cost(t), "date": _to_date(_trade_date(t)), "source": "trade"})
            elif side == "SELL":
                if sd is not None and d < sd:
                    # sale before period: still consume lots but no gain record
                    record = False
                elif ed is not None and d > ed:
                    record = False
                else:
                    record = True
                if sd is not None and d < sd:
                    record = False
                proceeds = _cost(t)  # net proceeds
                remaining = q
                sold_cost = 0.0
                lots = by_isin.setdefault(isin, [])
                for lot in lots:
                    if remaining <= TOL:
                        break
                    if lot["qty"] <= TOL:
                        continue
                    take = min(lot["qty"], remaining)
                    ratio = take / lot["qty"] if lot["qty"] else 0.0
                    sold_cost += lot["cost"] * ratio
                    lot["qty"] -= take
                    lot["cost"] -= lot["cost"] * ratio
                    remaining -= take
                if record and remaining <= TOL:
                    gains.append({"security_id": isin, "sale_date": d, "quantity": q, "proceeds": proceeds, "cost": sold_cost, "gain": proceeds - sold_cost})
        else:
            a = obj
            _sid = a.security_id.strip().upper()
            lots_flat = [l for ls in by_isin.values() for l in ls]
            from corporate_actions.engine import _apply_action
            _apply_action(lots_flat, a, review)
    return gains


def calculate_unrealized_gains(transactions, actions=None, as_of_date=None, price_lookup=None):
    lots = get_fifo_lots(transactions, actions, as_of_date=as_of_date)
    by_isin = {}
    for lot in lots:
        by_isin.setdefault(lot["isin"], []).append(lot)
    out = []
    for isin, ls in by_isin.items():
        qty = sum(l["qty"] for l in ls)
        cost = sum(l["cost"] for l in ls)
        price = None
        if price_lookup is not None:
            try:
                price = price_lookup(isin, as_of_date)
            except Exception:  # noqa: BLE001
                price = None
        mv = (qty * price) if price is not None else None
        gain = (mv - cost) if mv is not None else None
        out.append({"security_id": isin, "quantity": qty, "cost": cost, "price": price, "market_value": mv, "unrealized_gain": gain})
    return out


def get_stock_profit_summary(transactions, actions=None, security_id=None, as_of_date=None, price_lookup=None, dividends=None):
    sid = str(security_id).strip().upper() if security_id else None
    lots = get_fifo_lots(transactions, actions, security_id=sid, as_of_date=as_of_date)
    remaining_qty = sum(l["qty"] for l in lots)
    remaining_cost = sum(l["cost"] for l in lots)
    buys = [t for t in (transactions or []) if _isin(t) == sid and _side(t) in ("BUY", "OPENING", "OPEN", "OPENING_POSITION")]
    sells = [t for t in (transactions or []) if _isin(t) == sid and _side(t) == "SELL"]
    bought_qty = sum(_qty(t) for t in buys)
    sold_qty = sum(_qty(t) for t in sells)
    acq_cost = sum(_cost(t) for t in buys)
    proceeds = sum(_cost(t) for t in sells)
    realized = [g for g in calculate_realized_gains(transactions, actions, None, as_of_date) if g["security_id"] == sid]
    realized_gain = sum(g["gain"] for g in realized)
    price = price_lookup(sid, as_of_date) if (price_lookup and sid) else None
    mv = (remaining_qty * price) if price is not None else None
    unrealized = (mv - remaining_cost) if mv is not None else None
    div_total = 0.0
    if dividends:
        for d in dividends:
            if (d.get("security_id") or d.get("isin")) == sid:
                div_total += float(d.get("amount") or d.get("gross") or 0.0)
    total_gain = realized_gain + (unrealized or 0.0) + div_total
    return {
        "security_id": sid,
        "bought_qty": bought_qty,
        "sold_qty": sold_qty,
        "remaining_qty": remaining_qty,
        "acquisition_cost": acq_cost,
        "sale_proceeds": proceeds,
        "realized_gain": realized_gain,
        "unrealized_gain": unrealized,
        "dividends": div_total,
        "total_economic_gain": total_gain,
        "remaining_cost": remaining_cost,
        "market_value": mv,
    }
