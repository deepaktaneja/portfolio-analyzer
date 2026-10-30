from datetime import date

from dividends.dividends import (
    DividendCredit,
    DividendEvent,
    calculate_dividend_entitlement,
    get_dividend_receivables,
    get_portfolio_dividend_income,
    get_stock_dividend_cashflows,
    prevent_duplicate,
    reconcile_dividend_payment,
    reinvest_dividend,
)

TX = [
    {"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10},
]


def test_entitlement():
    ev = DividendEvent(security_id="INE1", ex_date=date(2024,6,1), dividend_per_share=5.0)
    ent = calculate_dividend_entitlement(TX, ev)
    assert ent["eligible_quantity"] == 10
    assert ent["expected_gross"] == 50.0

def test_reconcile_confirmed():
    ev = DividendEvent(security_id="INE1", ex_date=date(2024,6,1), dividend_per_share=5.0)
    ent = calculate_dividend_entitlement(TX, ev)
    cr = DividendCredit(security_id="INE1", ex_date=date(2024,6,1), gross_amount=50.0, tds=5.0, net_amount=45.0, credit_date=date(2024,6,15), status="confirmed")
    rec = reconcile_dividend_payment(ent, cr)
    assert rec["status"] == "confirmed"
    assert rec["tds"] == 5.0
    assert rec["net_credit"] == 45.0

def test_reconcile_missing():
    ev = DividendEvent(security_id="INE1", ex_date=date(2024,6,1), dividend_per_share=5.0)
    ent = calculate_dividend_entitlement(TX, ev)
    rec = reconcile_dividend_payment(ent, None)
    assert rec["status"] == "missing"

def test_reconcile_disputed():
    ev = DividendEvent(security_id="INE1", ex_date=date(2024,6,1), dividend_per_share=5.0)
    ent = calculate_dividend_entitlement(TX, ev)
    cr = DividendCredit(security_id="INE1", ex_date=date(2024,6,1), gross_amount=40.0, status="confirmed")
    rec = reconcile_dividend_payment(ent, cr)
    assert rec["status"] == "disputed"


def test_exited_stock_retains_history():
    tx = TX + [{"trade_date": "2024-07-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 10}]
    ev = DividendEvent(security_id="INE1", ex_date=date(2024,6,1), dividend_per_share=5.0)
    ent = calculate_dividend_entitlement(tx, ev)
    assert ent["eligible_quantity"] == 10

def test_cashflows_verified_only():
    ev = DividendEvent(security_id="INE1", ex_date=date(2024,6,1), dividend_per_share=5.0, payment_date=date(2024,6,15))
    cr = DividendCredit(security_id="INE1", ex_date=date(2024,6,1), gross_amount=50.0, credit_date=date(2024,6,15), status="confirmed")
    cfs = get_stock_dividend_cashflows([ev], [cr], verified_only=True)
    assert cfs == [(date(2024,6,15), 50.0, "INE1", date(2024,6,1))]
    cr2 = DividendCredit(security_id="INE1", ex_date=date(2024,6,1), gross_amount=50.0, status="expected")
    cfs2 = get_stock_dividend_cashflows([ev], [cr2], verified_only=True)
    assert cfs2 == []

def test_multiple_payments():
    evs = [
        DividendEvent(security_id="INE1", ex_date=date(2024,3,1), dividend_per_share=2.0),
        DividendEvent(security_id="INE1", ex_date=date(2024,9,1), dividend_per_share=3.0),
    ]
    ents = [calculate_dividend_entitlement(TX, e) for e in evs]
    assert len(ents) == 2

def test_duplicate_prevention():
    ev = DividendEvent(security_id="INE1", ex_date=date(2024,6,1), dividend_per_share=5.0, event_id="X")
    evs = prevent_duplicate([ev, ev])
    assert len(evs) == 1


def test_reinvest():
    ev = DividendEvent(security_id="INE1", ex_date=date(2024,6,1), dividend_per_share=5.0)
    cr = DividendCredit(security_id="INE1", ex_date=date(2024,6,1), gross_amount=50.0, net_amount=45.0, credit_date=date(2024,6,15), status="confirmed")
    tx = reinvest_dividend(ev, cr, price=10.0)
    assert tx["buy_sell"] == "BUY"
    assert tx["quantity"] == 4.5

def test_portfolio_income():
    ev = DividendEvent(security_id="INE1", ex_date=date(2024,6,1), dividend_per_share=5.0)
    cr = DividendCredit(security_id="INE1", ex_date=date(2024,6,1), gross_amount=50.0, tds=5.0, net_amount=45.0, status="confirmed")
    inc = get_portfolio_dividend_income([ev], [cr], TX)
    assert inc["total_gross"] == 50.0
    assert inc["total_tds"] == 5.0
    assert inc["total_net"] == 45.0

def test_receivables():
    ev = DividendEvent(security_id="INE1", ex_date=date(2024,6,1), dividend_per_share=5.0, payment_date=date(2024,6,15))
    recs = get_dividend_receivables([ev], [], date(2024,6,5))
    assert len(recs) == 1
    recs2 = get_dividend_receivables([ev], [], date(2024,6,20))
    assert len(recs2) == 0

