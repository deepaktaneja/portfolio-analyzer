from datetime import date

from corporate_actions.engine import (
    reconstruct_lots_with_actions,
    reconstruct_with_actions,
    review_flags,
)
from corporate_actions.models import CorporateAction

TX = [
    {"trade_date": "2018-06-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0},
]

def test_split():
    actions = [CorporateAction(security_id="INE1", event_type="split", effective_date=date(2020,1,1), ratio=2.0, verified=True)]
    qty, _ = reconstruct_with_actions(TX, actions, date(2020,2,1))
    assert qty["INE1"] == 20
    lots, _ = reconstruct_lots_with_actions(TX, actions, date(2020,2,1))
    assert lots[0]["qty"] == 20
    assert lots[0]["cost"] == 1000.0

def test_bonus():
    actions = [CorporateAction(security_id="INE1", event_type="bonus", effective_date=date(2020,1,1), ratio=1.0, verified=True)]
    qty, _ = reconstruct_with_actions(TX, actions, date(2020,2,1))
    assert qty["INE1"] == 20
    lots, _ = reconstruct_lots_with_actions(TX, actions, date(2020,2,1))
    # original + bonus
    assert len(lots) == 2
    assert any(l["source"] == "bonus" for l in lots)

def test_rights():
    tx = [{"trade_date": "2020-01-01", "isin": "INE2", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    actions = [CorporateAction(security_id="INE2", event_type="rights", effective_date=date(2020,2,1), ratio=0.5, subscription_price=50.0, subscribed_quantity=5, verified=True)]
    qty, _ = reconstruct_with_actions(tx, actions, date(2020,3,1))
    assert qty["INE2"] == 15
    assert qty.get("INE2-RTS", 0) == 0

def test_rights_sale_expiry():
    tx = [{"trade_date": "2020-01-01", "isin": "INE3", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    actions = [CorporateAction(security_id="INE3", event_type="rights", effective_date=date(2020,2,1), ratio=1.0, sold_entitlement_quantity=3, expired=True, verified=True)]
    qty, _ = reconstruct_with_actions(tx, actions, date(2020,3,1))
    assert qty["INE3"] == 10
    assert "INE3-RTS" not in qty

def test_merger():
    tx = [{"trade_date": "2019-01-01", "isin": "OLD", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    actions = [CorporateAction(security_id="OLD", event_type="merger", effective_date=date(2020,1,1), ratio=2.0, successor_id="NEW", verified=True)]
    qty, _ = reconstruct_with_actions(tx, actions, date(2020,2,1))
    assert qty.get("OLD", 0) == 0
    assert qty["NEW"] == 20

def test_demergers_unverified_flagged():
    tx = [{"trade_date": "2019-01-01", "isin": "OLD", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    actions = [CorporateAction(security_id="OLD", event_type="demerger", effective_date=date(2020,1,1), ratio=0.5, successor_id="NEW", verified=False)]
    qty, _ = reconstruct_with_actions(tx, actions, date(2020,2,1))
    assert _
    assert "NEW" in qty

def test_buyback():
    tx = [{"trade_date": "2019-01-01", "isin": "INE4", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    actions = [CorporateAction(security_id="INE4", event_type="buyback", effective_date=date(2020,1,1), ratio=4.0, verified=True)]
    qty, _ = reconstruct_with_actions(tx, actions, date(2020,2,1))
    assert qty["INE4"] == 6

def test_pre2019_split():
    tx = [{"trade_date": "2017-06-01", "isin": "INE5", "buy_sell": "BUY", "quantity": 5, "net_amount": 500.0}]
    actions = [CorporateAction(security_id="INE5", event_type="split", effective_date=date(2020,1,1), ratio=2.0, verified=True)]
    qty, _ = reconstruct_with_actions(tx, actions, date(2020,2,1))
    assert qty["INE5"] == 10
    lots, _ = reconstruct_lots_with_actions(tx, actions, date(2020,2,1))
    assert lots[0]["cost"] == 500.0

def test_original_transactions_immutable():
    tx = [{"trade_date": "2019-01-01", "isin": "INE6", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    before = [dict(t) for t in tx]
    actions = [CorporateAction(security_id="INE6", event_type="split", effective_date=date(2020,1,1), ratio=2.0, verified=True)]
    reconstruct_with_actions(tx, actions, date(2020,2,1))
    assert tx == before

def test_review_flags():
    actions = [CorporateAction(security_id="INE7", event_type="merger", effective_date=date(2020,1,1), ratio=1.0, successor_id="NEW", verified=False)]
    flags = review_flags(actions, date(2020,2,1))
    assert flags

def test_holdings_before_action():
    tx = [{"trade_date": "2019-01-01", "isin": "INE8", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    actions = [CorporateAction(security_id="INE8", event_type="split", effective_date=date(2020,1,1), ratio=2.0, verified=True)]
    qty, _ = reconstruct_with_actions(tx, actions, date(2019,6,1))
    assert qty["INE8"] == 10

def test_stock_dividend():
    tx = [{"trade_date": "2019-01-01", "isin": "INE9", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    actions = [CorporateAction(security_id="INE9", event_type="stock_dividend", effective_date=date(2020,1,1), ratio=0.1, verified=True)]
    qty, _ = reconstruct_with_actions(tx, actions, date(2020,2,1))
    assert qty["INE9"] == 11
