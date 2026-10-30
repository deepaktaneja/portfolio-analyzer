"""Dividend entitlement and payment engine (Stage 05)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

TOL = 1e-9

def _to_date(v):
    if isinstance(v, date):
        return v
    from datetime import datetime
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                return datetime.strptime(v.strip(), fmt).date()
            except ValueError:
                continue
    raise ValueError(f"Cannot parse date: {v!r}")


@dataclass
class DividendEvent:
    security_id: str
    ex_date: date
    record_date: date | None = None
    payment_date: date | None = None
    dividend_per_share: float = 0.0
    dividend_type: str = "cash"
    source: str = "manual"
    verification_status: str = "declared"
    event_id: str | None = None
    details: dict = field(default_factory=dict)

    def key(self) -> str:
        return self.event_id or f"{self.security_id.strip().upper()}|{self.ex_date.isoformat()}"

@dataclass
class DividendCredit:
    security_id: str
    ex_date: date | None = None
    gross_amount: float = 0.0
    tds: float = 0.0
    net_amount: float = 0.0
    credit_date: date | None = None
    status: str = "confirmed"
    credit_id: str | None = None

    @property
    def net(self) -> float:
        return self.net_amount if self.net_amount else (self.gross_amount - self.tds)

def _eligible_date(event: DividendEvent) -> date:
    return event.ex_date - __import__("datetime").timedelta(days=1)


def calculate_dividend_entitlement(transactions, event: DividendEvent, settlement_days: int = 0) -> dict:
    from ledger.positions import get_holdings
    sid = event.security_id.strip().upper()
    elig_date = event.ex_date - __import__("datetime").timedelta(days=1)
    holdings = get_holdings(transactions, elig_date, date_basis="trade")
    qty = float(holdings.get(sid, 0.0))
    expected_gross = qty * float(event.dividend_per_share or 0.0)
    return {
        "security_id": sid,
        "ex_date": event.ex_date,
        "record_date": event.record_date,
        "eligible_quantity": qty,
        "dividend_per_share": float(event.dividend_per_share or 0.0),
        "expected_gross": expected_gross,
        "event_key": event.key(),
    }

def reconcile_dividend_payment(expected: dict, actual) -> dict:
    exp_gross = float(expected.get("expected_gross", 0.0))
    if actual is None:
        return {
            "security_id": expected.get("security_id"),
            "ex_date": expected.get("ex_date"),
            "expected_gross": exp_gross,
            "actual_gross": 0.0,
            "tds": 0.0,
            "net_credit": 0.0,
            "difference": -exp_gross,
            "status": "missing",
        }
    ag = float(actual.gross_amount or 0.0)
    diff = ag - exp_gross
    if abs(diff) <= 1e-6:
        status = "confirmed" if getattr(actual, "status", "") == "confirmed" else getattr(actual, "status", "confirmed")
    else:
        status = "disputed"
    if getattr(actual, "status", "") == "missing":
        status = "missing"
    return {
        "security_id": expected.get("security_id"),
        "ex_date": expected.get("ex_date"),
        "expected_gross": exp_gross,
        "actual_gross": ag,
        "tds": float(actual.tds or 0.0),
        "net_credit": float(actual.net if hasattr(actual, "net") else (actual.net_amount or (actual.gross_amount - actual.tds))),
        "difference": diff,
        "status": status,
    }


def get_stock_dividend_cashflows(events, credits, security_id=None, verified_only=True):
    creds = {}
    for c in (credits or []):
        creds[(c.security_id.strip().upper(), c.ex_date)] = c
    out = []
    for e in (events or []):
        sid = e.security_id.strip().upper()
        if security_id and sid != security_id.strip().upper():
            continue
        if e.dividend_type != "cash":
            continue
        credit = creds.get((sid, e.ex_date))
        if credit is None:
            if verified_only:
                continue
            pay_date = e.payment_date or e.ex_date
            gross = float(e.dividend_per_share or 0.0)
        else:
            if verified_only and getattr(credit, "status", "") != "confirmed":
                continue
            pay_date = credit.credit_date or e.payment_date or e.ex_date
            gross = float(credit.gross_amount or 0.0)
        if pay_date is None:
            continue
        out.append((pay_date, gross, sid, e.ex_date))
    return out


def get_portfolio_dividend_income(events, credits, transactions=None):
    total_gross = 0.0
    total_tds = 0.0
    total_net = 0.0
    by_security = {}
    for e in (events or []):
        sid = e.security_id.strip().upper()
        elig = calculate_dividend_entitlement(transactions, e) if transactions is not None else None
        exp_gross = elig["expected_gross"] if elig else 0.0
        credit = None
        for c in (credits or []):
            if c.security_id.strip().upper() == sid and c.ex_date == e.ex_date:
                credit = c
                break
        ag = float(credit.gross_amount) if credit else 0.0
        tds = float(credit.tds) if credit else 0.0
        net = float(credit.net) if credit else 0.0
        total_gross += ag if credit else exp_gross
        total_tds += tds
        total_net += net if credit else 0.0
        by_security.setdefault(sid, {"expected_gross": 0.0, "actual_gross": 0.0, "tds": 0.0, "net": 0.0})
        by_security[sid]["expected_gross"] += exp_gross
        by_security[sid]["actual_gross"] += ag
        by_security[sid]["tds"] += tds
        by_security[sid]["net"] += net
    return {"total_gross": total_gross, "total_tds": total_tds, "total_net": total_net, "by_security": by_security}

def reinvest_dividend(event: DividendEvent, credit: DividendCredit, price: float, reinvest_date=None):
    amt = float(credit.net if hasattr(credit, "net") else credit.net_amount or (credit.gross_amount - credit.tds))
    qty = amt / float(price) if price else 0.0
    rd = reinvest_date or credit.credit_date or event.payment_date or event.ex_date
    return {"trade_date": (rd.isoformat() if hasattr(rd, "isoformat") else str(rd)), "isin": event.security_id.strip().upper(), "buy_sell": "BUY", "quantity": qty, "net_amount": amt, "event_type": "dividend_reinvestment", "source": "dividend"}

def get_dividend_receivables(events, credits, as_of_date):
    cutoff = _to_date(as_of_date)
    creds = {(c.security_id.strip().upper(), c.ex_date): c for c in (credits or [])}
    out = []
    for e in (events or []):
        sid = e.security_id.strip().upper()
        credit = creds.get((sid, e.ex_date))
        pay_date = (credit.credit_date if credit else e.payment_date) or e.ex_date
        if e.ex_date <= cutoff < _to_date(pay_date):
            out.append({"security_id": sid, "ex_date": e.ex_date, "payment_date": pay_date, "status": "receivable"})
    return out

def prevent_duplicate(events):
    seen = set()
    out = []
    for e in (events or []):
        k = e.key()
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out

