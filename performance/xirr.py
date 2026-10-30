"""Performance and XIRR engine (Stage 07)."""
from __future__ import annotations

from datetime import date, datetime

from src.xirr import compute_xirr

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
    raise ValueError("bad date")


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


def _verified_dividends(dividends, security_id):
    out = []
    for d in dividends or []:
        if isinstance(d, dict):
            sid = (d.get("security_id") or d.get("isin") or "").strip().upper()
            if security_id and sid != security_id:
                continue
            if d.get("status") and d.get("status") != "confirmed":
                continue
            amt = d.get("gross") or d.get("amount") or d.get("gross_amount")
            dt = d.get("payment_date") or d.get("credit_date") or d.get("date")
            if amt is None or dt is None:
                continue
            out.append((_to_date(dt), float(amt)))
        else:
            sid = getattr(d, "security_id", "").strip().upper()
            if security_id and sid != security_id:
                continue
            if getattr(d, "status", "confirmed") != "confirmed":
                continue
            amt = getattr(d, "gross_amount", None)
            dt = getattr(d, "credit_date", None) or getattr(d, "payment_date", None)
            if amt is None or dt is None:
                continue
            out.append((_to_date(dt), float(amt)))
    return out


def _rights_flows(actions, security_id, start, end):
    out = []
    for a in actions or []:
        if str(getattr(a, "security_id", "")).strip().upper() != security_id:
            continue
        if str(getattr(a, "event_type", "")).strip().lower() != "rights":
            continue
        sq = float(getattr(a, "subscribed_quantity", 0.0) or 0.0)
        price = getattr(a, "subscription_price", None)
        if sq > 0 and price is not None:
            d = getattr(a, "effective_date", None) or getattr(a, "record_date", None) or getattr(a, "ex_date", None)
            if d is None:
                continue
            dd = _to_date(d)
            if start is not None and dd < start:
                continue
            if end is not None and dd > end:
                continue
            out.append((dd, -sq * float(price)))
    return out


def _holdings_qty(transactions, security_id, as_of):
    from ledger.positions import get_stock_quantity
    try:
        return get_stock_quantity(transactions, security_id, as_of, date_basis="trade")
    except Exception:  # noqa: BLE001
        return 0.0


def calculate_stock_xirr(transactions, actions=None, dividends=None, price_lookup=None, security_id=None, start_date=None, end_date=None):
    sid = (security_id or "").strip().upper()
    trades = [t for t in (transactions or []) if _isin(t) == sid and _is_trade(t)]
    trades.sort(key=lambda t: (_to_date(_trade_date(t)), str(t.get("trade_no") or t.get("order_no") or "")))
    if not trades:
        return {"xirr": None, "cashflows": [], "reason": "no_trades"}
    first_date = _to_date(_trade_date(trades[0]))
    sd = _to_date(start_date) if start_date is not None else first_date
    ed = _to_date(end_date) if end_date is not None else max(_to_date(_trade_date(t)) for t in trades)
    flows = []
    if sd > first_date:
        from datetime import timedelta
        oq = _holdings_qty(transactions, sid, sd - timedelta(days=1))
        if oq and price_lookup is not None:
            try:
                op = price_lookup(sid, sd)
            except Exception:  # noqa: BLE001
                op = None
            if op is not None:
                flows.append((sd, -oq * float(op), "opening_value"))
    for t in trades:
        dd = _to_date(_trade_date(t))
        if dd < sd or dd > ed:
            continue
        side = _side(t)
        amt = _cost(t)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            flows.append((dd, -amt, "buy"))
        elif side == "SELL":
            flows.append((dd, amt, "sell"))
    for dd, amt in _verified_dividends(dividends, sid):
        if sd <= dd <= ed:
            flows.append((dd, amt, "dividend"))
    for dd, amt in _rights_flows(actions, sid, sd, ed):
        flows.append((dd, amt, "rights_subscription"))
    rq = _holdings_qty(transactions, sid, ed)
    if rq and price_lookup is not None:
        try:
            tp = price_lookup(sid, ed)
        except Exception:  # noqa: BLE001
            tp = None
        if tp is not None:
            flows.append((ed, rq * float(tp), "terminal_value"))
    x = compute_xirr([(d, a) for d, a, _ in flows])
    return {"xirr": x, "cashflows": flows}


def calculate_portfolio_xirr(transactions, actions=None, dividends=None, price_lookup=None, start_date=None, end_date=None):
    txs = [t for t in (transactions or []) if _is_trade(t)]
    if not txs:
        return {"xirr": None, "cashflows": [], "reason": "no_trades"}
    first = min(_to_date(_trade_date(t)) for t in txs)
    sd = _to_date(start_date) if start_date is not None else first
    ed = _to_date(end_date) if end_date is not None else max(_to_date(_trade_date(t)) for t in txs)
    secs = sorted({_isin(t) for t in txs})
    flows = []
    if sd > first:
        ov = 0.0
        ok = True
        for sid in secs:
            q = _holdings_qty(transactions, sid, sd - __import__("datetime").timedelta(days=1))
            if q and price_lookup is not None:
                try:
                    p = price_lookup(sid, sd)
                except Exception:  # noqa: BLE001
                    p = None
                if p is not None:
                    ov += q * float(p)
                else:
                    ok = False
        if ok and ov:
            flows.append((sd, -ov, "opening_value"))
    for t in txs:
        dd = _to_date(_trade_date(t))
        if dd < sd or dd > ed:
            continue
        amt = _cost(t)
        side = _side(t)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            flows.append((dd, -amt, "buy"))
        elif side == "SELL":
            flows.append((dd, amt, "sell"))
    for sid in secs:
        for dd, amt in _verified_dividends(dividends, sid):
            if sd <= dd <= ed:
                flows.append((dd, amt, "dividend"))
    for sid in secs:
        for dd, amt in _rights_flows(actions, sid, sd, ed):
            flows.append((dd, amt, "rights_subscription"))
    tv = 0.0
    tok = True
    for sid in secs:
        q = _holdings_qty(transactions, sid, ed)
        if q and price_lookup is not None:
            try:
                p = price_lookup(sid, ed)
            except Exception:  # noqa: BLE001
                p = None
            if p is not None:
                tv += q * float(p)
            else:
                tok = False
    if tok and tv:
        flows.append((ed, tv, "terminal_value"))
    x = compute_xirr([(d, a) for d, a, _ in flows])
    return {"xirr": x, "cashflows": flows}


def calculate_investment_cycle_xirr(transactions, actions=None, dividends=None, price_lookup=None, security_id=None):
    sid = (security_id or "").strip().upper()
    trades = [t for t in (transactions or []) if _isin(t) == sid and _is_trade(t)]
    trades.sort(key=lambda t: (_to_date(_trade_date(t)), str(t.get("trade_no") or t.get("order_no") or "")))
    cycles = []
    current = []
    qty = 0.0
    for t in trades:
        current.append(t)
        if _side(t) in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            qty += _qty(t)
        elif _side(t) == "SELL":
            qty -= _qty(t)
        if abs(qty) <= TOL and current:
            res = calculate_stock_xirr(current, None, dividends, price_lookup, security_id=sid, start_date=None, end_date=None)
            cycles.append(res)
            current = []
            qty = 0.0
    if current:
        res = calculate_stock_xirr(current, None, dividends, price_lookup, security_id=sid, start_date=None, end_date=None)
        cycles.append(res)
    return cycles


def calculate_portfolio_twr(transactions, actions=None, dividends=None, price_lookup=None, start_date=None, end_date=None):
    txs = [t for t in (transactions or []) if _is_trade(t)]
    if not txs:
        return None
    first = min(_to_date(_trade_date(t)) for t in txs)
    sd = _to_date(start_date) if start_date is not None else first
    ed = _to_date(end_date) if end_date is not None else max(_to_date(_trade_date(t)) for t in txs)
    dates = {sd, ed}
    for t in txs:
        dd = _to_date(_trade_date(t))
        if sd <= dd <= ed:
            dates.add(dd)
    dates = sorted(dates)
    secs = sorted({_isin(t) for t in txs})
    def port_value(d):
        v = 0.0
        for sid in secs:
            from ledger.positions import get_stock_quantity
            q = get_stock_quantity(transactions, sid, d, date_basis="trade")
            if q and price_lookup is not None:
                try:
                    p = price_lookup(sid, d)
                except Exception:  # noqa: BLE001
                    p = None
                if p is not None:
                    v += q * float(p)
        return v
    cf_by_date = {d: 0.0 for d in dates}
    for t in txs:
        dd = _to_date(_trade_date(t))
        if dd not in cf_by_date:
            continue
        amt = _cost(t)
        side = _side(t)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            cf_by_date[dd] += amt
        elif side == "SELL":
            cf_by_date[dd] -= amt
    twr = 1.0
    for i in range(len(dates) - 1):
        v0 = port_value(dates[i])
        v1 = port_value(dates[i+1])
        cf = cf_by_date.get(dates[i+1], 0.0)
        if v0 and v0 > 0:
            r = (v1 - cf - v0) / v0
            twr *= (1.0 + r)
    return twr - 1.0 if len(dates) > 1 else None



def get_xirr_cashflow_audit(transactions, actions=None, dividends=None, price_lookup=None, security_id=None, start_date=None, end_date=None, scope="stock"):
    if scope == "portfolio":
        res = calculate_portfolio_xirr(transactions, actions, dividends, price_lookup, start_date, end_date)
    elif scope == "cycle":
        res = calculate_investment_cycle_xirr(transactions, actions, dividends, price_lookup, security_id=security_id)
    else:
        res = calculate_stock_xirr(transactions, actions, dividends, price_lookup, security_id=security_id, start_date=start_date, end_date=end_date)
    if isinstance(res, list):
        return [r.get("cashflows", []) for r in res]
    return res.get("cashflows", [])
