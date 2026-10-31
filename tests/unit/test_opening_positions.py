from ledger.opening import categorize_issue, infer_minimum_opening, reconcile_opening_with_snapshot

TX = [
    {"trade_date": "2020-01-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 40},
    {"trade_date": "2022-01-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 60},
]

def test_infer_minimum():
    res = infer_minimum_opening(TX)
    assert res["INE1"] == 100

def test_categorize():
    assert categorize_issue("INE1", TX) == "missing_opening"

def test_reconcile():
    snap = {"INE1": 0}
    res = reconcile_opening_with_snapshot(TX, None, snap, "2022-12-31")
    # opening = current - change = 0 - (-100) = 100
    assert res.get("INE1") == 100
