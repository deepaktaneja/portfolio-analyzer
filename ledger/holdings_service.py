from __future__ import annotations

from datetime import date, datetime

from corporate_actions.engine import reconstruct_with_actions
from corporate_actions.models import CorporateAction
from ledger.positions import _to_date


def _parse_date(v):
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.date() if isinstance(v, datetime) else v
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                from datetime import datetime as _dt
                return _dt.strptime(v.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _load_actions(actions=None, db_path=None):
    if actions is not None:
        out = []
        for a in actions:
            if isinstance(a, CorporateAction):
                out.append(a)
            elif isinstance(a, dict):
                out.append(CorporateAction(
                    security_id=str(a.get("security_id") or a.get("security") or "").strip().upper(),
                    event_type=str(a.get("event_type") or a.get("type") or "").strip().lower(),
                    ex_date=_parse_date(a.get("ex_date")),
                    record_date=_parse_date(a.get("record_date")),
                    effective_date=_parse_date(a.get("effective_date")),
                    ratio=a.get("ratio"),
                    successor_id=a.get("successor_id") or a.get("successor"),
                    verified=bool(a.get("verified")),
                    source=a.get("source") or "manual",
                    verification_status=a.get("verification_status"),
                ))
        seen = set()
        deduped = []
        for a in out:
            key = (getattr(a, "security_id", None), getattr(a, "event_type", None),
                   str(getattr(a, "ex_date", None) or getattr(a, "effective_date", None) or getattr(a, "record_date", None)),
                   getattr(a, "ratio", None), getattr(a, "successor_id", None))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(a)
        return deduped
    try:
        from market_data.repository import MarketDataStore
        rows = MarketDataStore(db_path or "data/market_data.sqlite3").get_corporate_actions()
        return _load_actions(rows, db_path=db_path)
    except Exception:
        return []


def _tx_with_openings(transactions, opening_positions):
    tx = [dict(t) for t in (transactions or [])]
    for op in (opening_positions or []):
        try:
            od = op.get("ledger_opening_date") if isinstance(op, dict) else getattr(op, "ledger_opening_date", None)
            from ledger.positions import _to_date
            od = _to_date(od)
        except Exception:
            continue
        if od is None:
            continue
        isin = op.get("security_id") if isinstance(op, dict) else getattr(op, "security_id", None)
        qty = op.get("opening_qty") if isinstance(op, dict) else getattr(op, "opening_qty", None)
        tx.append({"trade_date": od.isoformat(), "isin": isin, "buy_sell": "OPENING", "quantity": qty, "event_type": "opening"})
    return tx


def get_holdings(as_of_date, transactions=None, actions=None, opening_positions=None, db_path=None):
    tx = _tx_with_openings(transactions, opening_positions)
    acts = _load_actions(actions, db_path=db_path)
    qty, _ = reconstruct_with_actions(tx, acts, as_of_date)
    return qty


def get_stock_quantity(security_id, as_of_date, transactions=None, actions=None, opening_positions=None, db_path=None):
    holdings = get_holdings(as_of_date, transactions=transactions, actions=actions, opening_positions=opening_positions, db_path=db_path)
    return float(holdings.get(str(security_id).strip().upper(), 0.0))

def get_quantity_movements(security_id, as_of_date, transactions=None, actions=None, opening_positions=None, db_path=None):
    sid = str(security_id).strip().upper()
    cutoff = _to_date(as_of_date)
    txs = []
    for op in (opening_positions or []):
        try:
            od = op.get("ledger_opening_date") if isinstance(op, dict) else getattr(op, "ledger_opening_date", None)
            od = _to_date(od)
        except Exception:
            continue
        if od is None or od > cutoff:
            continue
        isin = op.get("security_id") if isinstance(op, dict) else getattr(op, "security_id", None)
        if str(isin or "").strip().upper() != sid:
            continue
        qty = op.get("opening_qty") if isinstance(op, dict) else getattr(op, "opening_qty", None)
        txs.append({"date": od, "type": "OPENING", "qty": float(qty or 0), "source": "opening_position", "priority": 0})
    for t in (transactions or []):
        try:
            d = _to_date(t.get("trade_date") or t.get("settlement_date") or t.get("date"))
        except Exception:
            continue
        if d > cutoff:
            continue
        if str(t.get("isin") or t.get("security") or "").strip().upper() != sid:
            continue
        side = str(t.get("buy_sell") or t.get("side") or "").strip().upper()
        q = float(t.get("quantity") or 0)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            txs.append({"date": d, "type": "BUY", "qty": q, "source": t.get("source_file") or "transaction", "priority": 1})
        elif side == "SELL":
            txs.append({"date": d, "type": "SELL", "qty": -q, "source": t.get("source_file") or "transaction", "priority": 1})
    acts = _load_actions(actions, db_path=db_path)
    for a in acts:
        if str(a.security_id).strip().upper() != sid:
            continue
        try:
            d = _to_date(a.effective_date or a.record_date or a.ex_date)
        except Exception:
            continue
        if d > cutoff:
            continue
        et = a.event_type.lower()
        if et == "split":
            txs.append({"date": d, "type": "SPLIT", "qty": None, "ratio": a.ratio, "source": a.source or "corporate_action", "note": "ratio %s" % a.ratio, "priority": 2})
        elif et in ("bonus", "stock_dividend"):
            txs.append({"date": d, "type": "BONUS", "qty": None, "ratio": a.ratio, "source": a.source or "corporate_action", "note": "ratio %s" % a.ratio, "priority": 2})
        elif et == "rights":
            txs.append({"date": d, "type": "RIGHTS", "qty": float(a.subscribed_quantity or 0.0), "source": a.source or "corporate_action", "note": "ratio %s" % a.ratio, "priority": 2})
        elif et in ("merger", "demerger"):
            txs.append({"date": d, "type": et.upper(), "qty": None, "ratio": a.ratio, "source": a.source or "corporate_action", "note": "ratio %s -> %s" % (a.ratio, a.successor_id), "priority": 2, "terminal": True})
        elif et == "buyback":
            q = float(a.ratio or 0.0) or float(a.subscribed_quantity or 0.0)
            txs.append({"date": d, "type": "BUYBACK", "qty": None, "buyback_qty": q, "source": a.source or "corporate_action", "note": "qty %s" % q, "priority": 2})
    txs.sort(key=lambda x: (x["date"], x.get("priority", 1)))
    bal = 0.0
    out = []
    for e in txs:
        qty = e.get("qty")
        if qty is None:
            t = e.get("type")
            if t == "SPLIT":
                qty = bal * (float(e.get("ratio") or 1.0) - 1.0)
            elif t == "BONUS":
                qty = bal * float(e.get("ratio") or 0.0)
            elif t in ("MERGER", "DEMERGER"):
                qty = -bal
            elif t == "BUYBACK":
                qty = -min(bal, float(e.get("buyback_qty") or 0.0))
            else:
                qty = 0.0
            e["qty"] = qty
        bal += float(qty or 0.0)
        out.append({"date": e["date"].isoformat(), "type": e["type"], "qty": e.get("qty"), "running_balance": bal, "source": e.get("source"), "note": e.get("note")})
    return out
def get_holdings_reconciliation(as_of_date, transactions=None, actions=None, opening_positions=None, snapshot=None, db_path=None):
    recon = get_holdings(as_of_date, transactions=transactions, actions=actions, opening_positions=opening_positions, db_path=db_path)
    snap = {}
    if isinstance(snapshot, dict):
        for k, v in snapshot.items():
            snap[str(k).strip().upper()] = float(v or 0)
    elif snapshot is not None:
        for row in snapshot:
            isin = str(row.get("isin") or row.get("security") or "").strip().upper()
            if isin:
                snap[isin] = float(row.get("quantity") or row.get("qty") or 0)
    rows = []
    for isin in sorted(set(recon) | set(snap)):
        r = float(recon.get(isin, 0.0))
        s = float(snap.get(isin, 0.0))
        diff = r - s
        if abs(diff) > 1e-9:
            rows.append({"isin": isin, "reconstructed_qty": r, "broker_qty": s, "diff": diff})
    return rows
def get_holdings_reconciliation(as_of_date, transactions=None, actions=None, opening_positions=None, snapshot=None, db_path=None):
    recon = get_holdings(as_of_date, transactions=transactions, actions=actions, opening_positions=opening_positions, db_path=db_path)
    base = get_holdings(as_of_date, transactions=transactions, actions=[], opening_positions=opening_positions, db_path=db_path)
    totals = {}
    for t in (transactions or []):
        try:
            d = _to_date(t.get("trade_date") or t.get("settlement_date") or t.get("date"))
        except Exception:
            continue
        if d > _to_date(as_of_date):
            continue
        isin = str(t.get("isin") or t.get("security") or "").strip().upper()
        if not isin:
            continue
        q = float(t.get("quantity") or 0)
        side = str(t.get("buy_sell") or t.get("side") or "").strip().upper()
        tot = totals.setdefault(isin, {"opening": 0.0, "purchases": 0.0, "sales": 0.0})
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            if side == "OPENING":
                tot["opening"] += q
            else:
                tot["purchases"] += q
        elif side == "SELL":
            tot["sales"] += q
    for op in (opening_positions or []):
        try:
            od = op.get("ledger_opening_date") if isinstance(op, dict) else getattr(op, "ledger_opening_date", None)
            od = _to_date(od)
        except Exception:
            continue
        if od is None or od > _to_date(as_of_date):
            continue
        isin = op.get("security_id") if isinstance(op, dict) else getattr(op, "security_id", None)
        isin = str(isin or "").strip().upper()
        if not isin:
            continue
        qty = op.get("opening_qty") if isinstance(op, dict) else getattr(op, "opening_qty", None)
        totals.setdefault(isin, {"opening": 0.0, "purchases": 0.0, "sales": 0.0})["opening"] += float(qty or 0)
    snap = {}
    if isinstance(snapshot, dict):
        for k, v in snapshot.items():
            snap[str(k).strip().upper()] = float(v or 0)
    elif snapshot is not None:
        for row in snapshot:
            isin = str(row.get("isin") or row.get("security") or "").strip().upper()
            if isin:
                snap[isin] = float(row.get("quantity") or row.get("qty") or 0)
    rows = []
    for isin in sorted(set(recon) | set(snap)):
        r = float(recon.get(isin, 0.0))
        s = float(snap.get(isin, 0.0))
        diff = r - s
        t = totals.get(isin, {"opening": 0.0, "purchases": 0.0, "sales": 0.0})
        ca = r - float(base.get(isin, 0.0))
        classification = "reconciled" if abs(diff) <= 1e-9 else "unresolved"
        if classification != "reconciled":
            if t["opening"] == 0 and t["purchases"] == 0 and s > 0:
                classification = "missing_opening"
            elif ca != 0 and abs(diff) <= abs(ca) + 1e-9:
                classification = "missing_corporate_action"
            elif t["sales"] > t["purchases"] + t["opening"]:
                classification = "missing_opening"
            elif s == 0 and r > 0:
                classification = "missing_broker_record"
        if abs(diff) <= 1e-9:
            continue
        rows.append({"isin": isin, "opening_qty": t["opening"], "purchases_qty": t["purchases"], "sales_qty": t["sales"], "corporate_adjustment_qty": ca, "reconstructed_qty": r, "broker_qty": s, "diff": diff, "classification": classification})
    return rows

def get_reverse_opening_candidates(as_of_date, transactions=None, snapshot=None, actions=None, opening_positions=None, db_path=None):
    from ledger.opening import reconcile_opening_with_snapshot
    return reconcile_opening_with_snapshot(transactions, actions, snapshot, as_of_date)
