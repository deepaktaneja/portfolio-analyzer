"""Performance and XIRR engine (Stage 07)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from ledger.positions import reconstruct
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


def _holdings_as_of(transactions, as_of, opening_positions=None):
    cutoff = _to_date(as_of)
    quantities = defaultdict(float)
    for op in (opening_positions or []):
        try:
            od = _to_date(op.get("ledger_opening_date") if isinstance(op, dict) else op.ledger_opening_date)
        except Exception:  # noqa: BLE001
            continue
        if od is not None and od <= cutoff:
            isin = op.get("security_id") if isinstance(op, dict) else op.security_id
            isin = str(isin or "").strip().upper()
            if not isin:
                continue
            qty = op.get("opening_qty") if isinstance(op, dict) else op.opening_qty
            try:
                quantities[isin] += float(qty or 0.0)
            except (TypeError, ValueError):
                pass
    for t in (transactions or []):
        if not _is_trade(t):
            continue
        dd = _to_date(_trade_date(t))
        if dd <= cutoff:
            isin = _isin(t)
            if not isin:
                continue
            side = _side(t)
            q = _qty(t)
            if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
                quantities[isin] += q
            elif side == "SELL":
                quantities[isin] -= q
    return quantities


def _holdings_qty(transactions, security_id, as_of, opening_positions=None):
    return float(_holdings_as_of(transactions, as_of, opening_positions).get(str(security_id).strip().upper(), 0.0))


def calculate_stock_xirr(transactions, actions=None, dividends=None, price_lookup=None, security_id=None, start_date=None, end_date=None, opening_positions=None):
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
        oq = _holdings_qty(transactions, sid, sd - timedelta(days=1), opening_positions)
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
    rq = max(0.0, _holdings_qty(transactions, sid, ed, opening_positions))
    if rq and price_lookup is not None:
        try:
            tp = price_lookup(sid, ed)
        except Exception:  # noqa: BLE001
            tp = None
        if tp is not None:
            flows.append((ed, rq * float(tp), "terminal_value"))
    x = compute_xirr([(d, a) for d, a, _ in flows])
    if rq and flows and flows[-1][2] != "terminal_value":
        return {
            "xirr": None,
            "cashflows": flows,
            "reason": "missing_terminal_price",
            "missing_prices": [sid],
        }
    return {"xirr": x, "cashflows": flows}


def calculate_portfolio_xirr(transactions, actions=None, dividends=None, price_lookup=None, start_date=None, end_date=None, holdings_at_end=None, opening_positions=None):
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
        holdings_before = _holdings_as_of(transactions, sd - __import__("datetime").timedelta(days=1), opening_positions)
        for sid in secs:
            q = holdings_before.get(sid, 0.0)
            if q and price_lookup is not None:
                try:
                    p = price_lookup(sid, sd)
                except Exception:  # noqa: BLE001
                    p = None
                if p is not None:
                    ov += float(q) * float(p)
                else:
                    ok = False
        if ov:
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
    holdings_ed = _holdings_as_of(transactions, ed, opening_positions)
    if holdings_at_end is not None:
        terminal_qty = {str(k).strip().upper(): max(0.0, float(v or 0.0)) for k, v in holdings_at_end.items()}
    else:
        terminal_qty = {k: max(0.0, float(v)) for k, v in holdings_ed.items()}
    terminal_sids = sorted(set(secs) | set(terminal_qty))
    missing_prices = []
    for sid in terminal_sids:
        q = terminal_qty.get(sid, 0.0)
        if q and price_lookup is not None:
            try:
                p = price_lookup(sid, ed)
            except Exception:  # noqa: BLE001
                p = None
            if p is not None:
                tv += float(q) * float(p)
            else:
                tok = False
                missing_prices.append(sid)
    if tv:
        flows.append((ed, tv, "terminal_value"))
    _amounts = [a for _, a, _ in flows]
    if not (_amounts and min(_amounts) < 0 < max(_amounts)):
        _reason = "invalid_cashflows"
    elif not tok and tv:
        _reason = "partial_terminal_price"
    elif not tok:
        _reason = "missing_terminal_price"
    else:
        _reason = None
    x = compute_xirr([(d, a) for d, a, _ in flows])
    if x is None and _reason is None:
        _reason = "solver_failed"
    if _reason in ("missing_terminal_price", "invalid_cashflows"):
        x = None
    return {
        "xirr": x,
        "cashflows": flows,
        "reason": _reason,
        "missing_prices": missing_prices,
    }


def calculate_investment_cycle_xirr(transactions, actions=None, dividends=None, price_lookup=None, security_id=None, opening_positions=None):
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


def calculate_portfolio_twr(transactions, actions=None, dividends=None, price_lookup=None, start_date=None, end_date=None, opening_positions=None):
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
    from collections import defaultdict
    dates = sorted(dates)
    # Precompute holdings per valuation date in a single pass.
    sd = dates[0] if dates else None
    ed = dates[-1] if dates else None
    quantities = defaultdict(float)
    # apply transactions/openings before sd
    if sd is not None:
        for t in txs:
            dd = _to_date(_trade_date(t))
            if dd < sd:
                isin = _isin(t)
                if not isin:
                    continue
                side = _side(t)
                q = _qty(t)
                if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
                    quantities[isin] += q
                elif side == "SELL":
                    quantities[isin] -= q
        for op in (opening_positions or []):
            try:
                od = _to_date(op.get("ledger_opening_date") if isinstance(op, dict) else op.ledger_opening_date)
            except Exception:  # noqa: BLE001
                continue
            if od is not None and od < sd:
                isin = op.get("security_id") if isinstance(op, dict) else op.security_id
                isin = str(isin or "").strip().upper()
                if not isin:
                    continue
                qty = op.get("opening_qty") if isinstance(op, dict) else op.opening_qty
                try:
                    quantities[isin] += float(qty or 0.0)
                except (TypeError, ValueError):
                    pass
    # group events by date within [sd, ed]
    events = defaultdict(lambda: {"trades": [], "openings": []})
    for t in txs:
        dd = _to_date(_trade_date(t))
        if sd is not None and ed is not None and sd <= dd <= ed:
            events[dd]["trades"].append(t)
    for op in (opening_positions or []):
        try:
            od = _to_date(op.get("ledger_opening_date") if isinstance(op, dict) else op.ledger_opening_date)
        except Exception:  # noqa: BLE001
            continue
        if od is not None and sd is not None and ed is not None and sd <= od <= ed:
            events[od]["openings"].append(op)
    holdings_by_date = {}
    for d in dates:
        for op in events[d]["openings"]:
            isin = op.get("security_id") if isinstance(op, dict) else op.security_id
            isin = str(isin or "").strip().upper()
            if not isin:
                continue
            qty = op.get("opening_qty") if isinstance(op, dict) else op.opening_qty
            try:
                quantities[isin] += float(qty or 0.0)
            except (TypeError, ValueError):
                pass
        for t in events[d]["trades"]:
            isin = _isin(t)
            if not isin:
                continue
            side = _side(t)
            q = _qty(t)
            if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
                quantities[isin] += q
            elif side == "SELL":
                quantities[isin] -= q
        if any(q < -TOL for q in quantities.values()):
            holdings_by_date[d] = {}
        else:
            holdings_by_date[d] = {k: v for k, v in quantities.items()}
    def port_value(d):
        v = 0.0
        for sid, q in holdings_by_date.get(d, {}).items():
            if q and price_lookup is not None:
                try:
                    p = price_lookup(sid, d)
                except Exception:  # noqa: BLE001
                    p = None
                if p is not None:
                    v += float(q) * float(p)
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
    _has_valid = False
    for i in range(len(dates) - 1):
        v0 = port_value(dates[i])
        v1 = port_value(dates[i+1])
        cf = cf_by_date.get(dates[i+1], 0.0)
        if v0 and v0 > 0:
            r = (v1 - cf - v0) / v0
            twr *= (1.0 + r)
            _has_valid = True
    return (twr - 1.0) if (_has_valid and len(dates) > 1) else None


def portfolio_value_series(transactions, price_lookup=None, start_date=None, end_date=None,
                           opening_positions=None, monthly=True):
    """Portfolio market value on periodic snapshots over ``[start_date, end_date]``.

    Risk analytics (volatility, Sharpe, Sortino, max drawdown) need a *value*
    series rather than irregular cash flows. Quantities are reconstructed as of
    each snapshot date and priced with ``price_lookup`` (which resolves the last
    known close on or before the date). Snapshot dates are the range endpoints
    plus each calendar month-end inside the range. Returns ``[(date, value)]``.

    A snapshot whose securities all lack a price contributes a ``0.0`` value, so
    callers can decide whether the coverage is good enough to use the series.
    """
    if price_lookup is None:
        return []
    txs = [t for t in (transactions or []) if _is_trade(t)]
    if not txs:
        return []
    first = min(_to_date(_trade_date(t)) for t in txs)
    sd = _to_date(start_date) if start_date is not None else first
    ed = _to_date(end_date) if end_date is not None else max(_to_date(_trade_date(t)) for t in txs)
    if sd > ed:
        return []
    snapshots = {sd, ed}
    if monthly:
        from datetime import timedelta
        cursor = date(sd.year, sd.month, 1)
        while cursor <= ed:
            nxt = date(cursor.year + (cursor.month // 12), (cursor.month % 12) + 1, 1)
            month_end = nxt - timedelta(days=1)
            if sd < month_end < ed:
                snapshots.add(month_end)
            cursor = nxt
    secs = sorted({_isin(t) for t in txs})
    out = []
    for d in sorted(snapshots):
        quantities = _holdings_as_of(transactions, d, opening_positions)
        value = 0.0
        for sid in sorted(set(secs) | set(quantities)):
            q = quantities.get(sid, 0.0)
            if not q:
                continue
            try:
                p = price_lookup(sid, d)
            except Exception:  # noqa: BLE001
                p = None
            if p is not None:
                value += float(q) * float(p)
        out.append((d, value))
    return out


def calculate_portfolio_snapshot(transactions, price_lookup=None, as_of_date=None,
                                 opening_positions=None):
    """Portfolio market value and cost basis as of a specific date.

    Reconstructs FIFO lots from opening positions + trades up to ``as_of_date``
    (corporate actions are intentionally not applied here, matching the holding
    layer used by :func:`calculate_portfolio_xirr`), values the remaining units
    with ``price_lookup`` and returns::

        portfolio_value  – sum(qty x price) of holdings at the date
        invested_capital – remaining FIFO cost basis of those holdings
        gains            – portfolio_value - invested_capital
        holdings         – {isin: quantity}
        missing_prices   – ISINs held at the date with no price

    ``as_of_date=None`` means "use every transaction" (no cutoff). Opening
    positions contribute their ``original_acq_cost`` as cost basis when known.
    """
    cutoff = _to_date(as_of_date) if as_of_date is not None else None
    events = []
    for op in (opening_positions or []):
        try:
            od = _to_date(op.get("ledger_opening_date") if isinstance(op, dict) else op.ledger_opening_date)
        except Exception:  # noqa: BLE001
            od = None
        if od is None or (cutoff is not None and od > cutoff):
            continue
        isin = str((op.get("security_id") if isinstance(op, dict) else getattr(op, "security_id", None)) or "").strip().upper()
        if not isin:
            continue
        qty = op.get("opening_qty") if isinstance(op, dict) else getattr(op, "opening_qty", None)
        cost = op.get("original_acq_cost") if isinstance(op, dict) else getattr(op, "original_acq_cost", None)
        try:
            qty = float(qty or 0.0)
        except (TypeError, ValueError):
            qty = 0.0
        try:
            cost = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost = None
        if qty > 0:
            events.append((od, 0, isin, "OPENING", qty, cost))
    for t in (transactions or []):
        if not _is_trade(t):
            continue
        dd = _to_date(_trade_date(t))
        if cutoff is not None and dd > cutoff:
            continue
        isin = _isin(t)
        if not isin:
            continue
        events.append((dd, 1, isin, _side(t), _qty(t), _cost(t)))
    events.sort(key=lambda e: (e[0], e[1]))

    lots: dict[str, list[list[float]]] = defaultdict(list)  # isin -> [[qty, total_cost], ...]
    for _dd, _order, isin, side, q, c in events:
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            if q <= 0:
                continue
            lots[isin].append([q, float(c or 0.0)])
        elif side == "SELL":
            remaining = q
            for lot in lots.get(isin, []):
                if remaining <= TOL:
                    break
                take = min(lot[0], remaining)
                ratio = (take / lot[0]) if lot[0] else 0.0
                lot[1] -= lot[1] * ratio
                lot[0] -= take
                remaining -= take
            lots[isin] = [lot for lot in lots.get(isin, []) if lot[0] > TOL]

    holdings: dict[str, float] = {}
    invested_capital = 0.0
    for isin, ls in lots.items():
        qty = sum(lot[0] for lot in ls)
        if qty <= TOL:
            continue
        holdings[isin] = qty
        invested_capital += sum(lot[1] for lot in ls)

    portfolio_value = 0.0
    missing = []
    for isin, qty in holdings.items():
        price = None
        if price_lookup is not None:
            try:
                price = price_lookup(isin, as_of_date)
            except Exception:  # noqa: BLE001
                price = None
        if price is None:
            missing.append(isin)
            continue
        portfolio_value += float(qty) * float(price)

    return {
        "as_of_date": cutoff,
        "portfolio_value": portfolio_value,
        "invested_capital": invested_capital,
        "gains": portfolio_value - invested_capital,
        "holdings": holdings,
        "missing_prices": sorted(missing),
    }


def get_xirr_cashflow_audit(transactions, actions=None, dividends=None, price_lookup=None, security_id=None, start_date=None, end_date=None, scope="stock", opening_positions=None):
    if scope == "portfolio":
        res = calculate_portfolio_xirr(transactions, actions, dividends, price_lookup, start_date, end_date, opening_positions=opening_positions)
    elif scope == "cycle":
        res = calculate_investment_cycle_xirr(transactions, actions, dividends, price_lookup, security_id=security_id, opening_positions=opening_positions)
    else:
        res = calculate_stock_xirr(transactions, actions, dividends, price_lookup, security_id=security_id, start_date=start_date, end_date=end_date, opening_positions=opening_positions)
    if isinstance(res, list):
        return [r.get("cashflows", []) for r in res]
    return res.get("cashflows", [])
