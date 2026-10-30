"""Corporate action engine (Stage 03)."""
from __future__ import annotations

from datetime import date

from corporate_actions.models import CorporateAction

TOL = 1e-9


def _to_date(v):
    if isinstance(v, date):
        return v
    from datetime import datetime
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(v.strip(), fmt).date()
            except ValueError:
                continue
    return date.min

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


def _build_lots(transactions):
    lots = []
    trades = [t for t in (transactions or []) if _side(t) in ("BUY", "OPENING", "OPEN", "OPENING_POSITION", "SELL")]
    trades.sort(key=lambda t: (_to_date(_trade_date(t)), str(t.get("trade_no") or t.get("order_no") or "")))
    by_isin = {}
    for t in trades:
        isin = _isin(t)
        if not isin:
            continue
        side = _side(t)
        q = _qty(t)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            by_isin.setdefault(isin, []).append({"isin": isin, "qty": q, "cost": _cost(t), "date": _to_date(_trade_date(t)), "source": "trade"})
        elif side == "SELL":
            remaining = q
            lots_list = by_isin.setdefault(isin, [])
            for lot in lots_list:
                if remaining <= TOL:
                    break
                if lot["qty"] <= TOL:
                    continue
                take = min(lot["qty"], remaining)
                ratio = take / lot["qty"] if lot["qty"] else 0.0
                lot["qty"] -= take
                lot["cost"] -= lot["cost"] * ratio
                remaining -= take
    for isin, ls in by_isin.items():
        for lot in ls:
            if lot["qty"] > TOL:
                lots.append(lot)
    return lots


def _apply_action(lots, action: CorporateAction, review: list):
    isin = action.security_id.strip().upper()
    affected = [l for l in lots if l["isin"] == isin]
    if not affected:
        return lots
    credit = action.credit_date()
    et = action.event_type.lower()
    if et == "split":
        ratio = float(action.ratio or 1.0)
        for lot in affected:
            lot["qty"] *= ratio
    elif et in ("bonus", "stock_dividend", "stock dividend"):
        ratio = float(action.ratio or 0.0)
        for lot in list(affected):
            lots.append({"isin": isin, "qty": lot["qty"] * ratio, "cost": 0.0, "date": credit, "source": "bonus"})
    elif et == "rights":
        ratio = float(action.ratio or 0.0)
        rts_id = isin + "-RTS"
        for lot in affected:
            lots.append({"isin": rts_id, "qty": lot["qty"] * ratio, "cost": 0.0, "date": credit, "source": "rights_entitlement"})
        # subscription consumes entitlements
        sq = float(action.subscribed_quantity or 0.0)
        if sq > 0:
            remaining = sq
            for lot in lots:
                if lot["isin"] != rts_id or remaining <= TOL:
                    continue
                if lot["qty"] <= TOL:
                    continue
                take = min(lot["qty"], remaining)
                lot["qty"] -= take
                remaining -= take
            if action.subscription_price is not None:
                lots.append({"isin": isin, "qty": sq, "cost": sq * float(action.subscription_price), "date": credit, "source": "rights_subscription"})
        # entitlement sale
        sold = float(action.sold_entitlement_quantity or 0.0)
        if sold > 0:
            # reduce rights lots FIFO
            remaining = sold
            for lot in lots:
                if lot["isin"] != rts_id or remaining <= TOL:
                    continue
                if lot["qty"] <= TOL:
                    continue
                take = min(lot["qty"], remaining)
                lot["qty"] -= take
                remaining -= take
        # expiry
        if action.expired:
            for lot in lots:
                if lot["isin"] == rts_id:
                    lot["qty"] = 0.0
    elif et in ("merger", "demerger"):
        succ = action.successor_id or isin
        ratio = float(action.ratio or 1.0)
        alloc = action.cost_allocation
        if not action.verified:
            review.append(f"{et} on {isin} requires manual verification")
        for lot in affected:
            lot["isin"] = succ
            lot["qty"] *= ratio
            if alloc is not None and action.verified:
                lot["cost"] *= float(alloc)
    elif et == "buyback":
        qty = float(action.ratio or 0.0) or float(action.subscribed_quantity or 0.0)
        # reduce FIFO
        remaining = qty
        for lot in lots:
            if lot["isin"] != isin or remaining <= TOL:
                continue
            if lot["qty"] <= TOL:
                continue
            take = min(lot["qty"], remaining)
            r = take / lot["qty"] if lot["qty"] else 0.0
            lot["qty"] -= take
            lot["cost"] -= lot["cost"] * r
            remaining -= take
    else:
        review.append(f"Unknown event type {et} on {isin}")
    # remove zero lots
    for l in list(lots):
        if l["qty"] <= TOL:
            lots.remove(l)
    return lots


def reconstruct_with_actions(transactions, actions, as_of_date):
    cutoff = _to_date(as_of_date) if not isinstance(as_of_date, date) else as_of_date
    lots = _build_lots(transactions)
    review = []
    acts = [a for a in (actions or []) if _to_date(a.effective_date or a.record_date or a.ex_date) <= cutoff]
    acts.sort(key=lambda a: _to_date(a.effective_date or a.record_date or a.ex_date))
    for a in acts:
        _apply_action(lots, a, review)
    # quantities
    qty = {}
    for lot in lots:
        qty[lot["isin"]] = qty.get(lot["isin"], 0.0) + lot["qty"]
    return {k: v for k, v in qty.items() if abs(v) > TOL}, review

def reconstruct_lots_with_actions(transactions, actions, as_of_date):
    cutoff = _to_date(as_of_date) if not isinstance(as_of_date, date) else as_of_date
    lots = _build_lots(transactions)
    review = []
    acts = [a for a in (actions or []) if _to_date(a.effective_date or a.record_date or a.ex_date) <= cutoff]
    acts.sort(key=lambda a: _to_date(a.effective_date or a.record_date or a.ex_date))
    for a in acts:
        _apply_action(lots, a, review)
    return [l for l in lots if l["qty"] > TOL], review

def review_flags(actions, as_of_date):
    cutoff = _to_date(as_of_date) if not isinstance(as_of_date, date) else as_of_date
    flags = []
    for a in (actions or []):
        d = _to_date(a.effective_date or a.record_date or a.ex_date)
        if d <= cutoff and not a.verified and a.event_type.lower() in ("merger", "demerger"):
            flags.append(f"{a.event_type} on {a.security_id} requires manual verification")
        if d <= cutoff and a.event_type.lower() == "rights" and not a.verified:
            flags.append(f"rights on {a.security_id} unverified")
    return flags

