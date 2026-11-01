from datetime import date

from corporate_actions.models import CorporateAction
from ledger.holdings_service import get_holdings, get_stock_quantity, get_holdings_reconciliation, get_quantity_movements, get_reverse_opening_candidates


TX = [{"trade_date": "2019-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]


def test_base_holdings():
    h = get_holdings("2019-06-01", transactions=TX)
    assert h["INE1"] == 10


def test_split():
    actions = [CorporateAction(security_id="INE1", event_type="split", ex_date=date(2020, 1, 1), ratio=2.0, verified=True)]
    h = get_holdings("2020-02-01", transactions=TX, actions=actions)
    assert h["INE1"] == 20


def test_bonus():
    actions = [CorporateAction(security_id="INE1", event_type="bonus", ex_date=date(2020, 1, 1), ratio=1.0, verified=True)]
    h = get_holdings("2020-02-01", transactions=TX, actions=actions)
    assert h["INE1"] == 20


def test_rights_subscription():
    actions = [CorporateAction(security_id="INE1", event_type="rights", ex_date=date(2020, 1, 1), ratio=1.0, subscribed_quantity=5, subscription_price=50.0, verified=True)]
    h = get_holdings("2020-02-01", transactions=TX, actions=actions)
    assert h["INE1"] == 15


def test_merger():
    tx = [{"trade_date": "2019-01-01", "isin": "OLD", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    actions = [CorporateAction(security_id="OLD", event_type="merger", ex_date=date(2020, 1, 1), ratio=2.0, successor_id="NEW", verified=True)]
    h = get_holdings("2020-02-01", transactions=tx, actions=actions)
    assert h.get("OLD", 0) == 0
    assert h["NEW"] == 20


def test_opening_position():
    ops = [{"security_id": "INE2", "ledger_opening_date": "2018-01-01", "opening_qty": 5}]
    tx = [{"trade_date": "2019-01-01", "isin": "INE2", "buy_sell": "SELL", "quantity": 2}]
    h = get_holdings("2019-06-01", transactions=tx, opening_positions=ops)
    assert h["INE2"] == 3


def test_duplicate_action_dedupe():
    actions = [
        CorporateAction(security_id="INE1", event_type="split", ex_date=date(2020, 1, 1), ratio=2.0, verified=True),
        CorporateAction(security_id="INE1", event_type="split", ex_date=date(2020, 1, 1), ratio=2.0, verified=True),
    ]
    h = get_holdings("2020-02-01", transactions=TX, actions=actions)
    assert h["INE1"] == 20


def test_reconciliation():
    snap = {"INE1": 20}
    rows = get_holdings_reconciliation("2020-02-01", transactions=TX, actions=[CorporateAction(security_id="INE1", event_type="split", ex_date=date(2020, 1, 1), ratio=2.0, verified=True)], snapshot=snap)
    assert rows == []


def test_reverse_opening_candidates():
    tx = [{"trade_date": "2019-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 3, "net_amount": 300.0}]
    snap = {"INE1": 10}
    cands = get_reverse_opening_candidates("2019-06-01", transactions=tx, snapshot=snap)
    assert cands["INE1"] == 7


def test_movement_report_split():
    tx = [{"trade_date": "2019-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    acts = [CorporateAction(security_id="INE1", event_type="split", ex_date=date(2020, 1, 1), ratio=2.0, verified=True)]
    moves = get_quantity_movements("INE1", "2020-02-01", transactions=tx, actions=acts)
    assert any(m["type"] == "SPLIT" for m in moves)
    assert moves[-1]["running_balance"] == 20
def test_movements():
    moves = get_quantity_movements("INE1", "2019-06-01", transactions=TX)
    assert len(moves) == 1
    assert moves[0]["type"] == "BUY"
def test_reconciliation_missing_opening():
    snap = {"INE1": 10}
    rows = get_holdings_reconciliation("2019-06-01", transactions=[], snapshot=snap)
    assert rows
    assert rows[0]["classification"] == "missing_opening"


def test_reconciliation_corporate_action():
    tx = [{"trade_date": "2019-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "net_amount": 1000.0}]
    actions = [CorporateAction(security_id="INE1", event_type="split", ex_date=date(2020, 1, 1), ratio=2.0, verified=True)]
    # before action, broker expects 10 but recon 10; no diff
    # after action date, broker stale 10 vs recon 20
    rows = get_holdings_reconciliation("2020-02-01", transactions=tx, actions=actions, snapshot={"INE1": 10})
    assert rows
    assert rows[0]["classification"] == "missing_corporate_action"
