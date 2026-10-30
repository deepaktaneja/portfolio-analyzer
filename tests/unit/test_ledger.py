from ledger.positions import (
    TransactionLedger,
    check_negative_holdings,
    get_holdings,
    get_stock_quantity,
    get_symbol_aliases,
    get_transaction_history,
    reconcile_holdings,
)

TX = [
    {"trade_date": "2024-01-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 10, "symbol": "ABC"},
    {"trade_date": "2024-02-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 5, "symbol": "ABC"},
    {"trade_date": "2024-03-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 7},
]

def test_multiple_purchases():
    h = get_holdings(TX, "2024-02-15")
    assert h["INE1"] == 15

def test_partial_sale():
    h = get_holdings(TX, "2024-03-15")
    assert h["INE1"] == 8

def test_full_exit():
    tx = TX + [{"trade_date": "2024-04-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 8}]
    h = get_holdings(tx, "2024-04-15")
    assert "INE1" not in h

def test_reentry():
    tx = TX + [
        {"trade_date": "2024-04-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 8},
        {"trade_date": "2024-05-01", "isin": "INE1", "buy_sell": "BUY", "quantity": 3},
    ]
    h = get_holdings(tx, "2024-05-15")
    assert h["INE1"] == 3

def test_missing_opening_position():
    # snapshot says 100, ledger only has 8 -> diff
    diffs = reconcile_holdings(TX, "2024-03-15", {"INE1": 100})
    assert len(diffs) == 1
    assert diffs[0]["diff"] == 8 - 100

def test_same_day_transactions():
    tx = [
        {"trade_date": "2024-01-01", "isin": "INE2", "buy_sell": "BUY", "quantity": 4},
        {"trade_date": "2024-01-01", "isin": "INE2", "buy_sell": "SELL", "quantity": 1},
    ]
    h = get_holdings(tx, "2024-01-01")
    assert h["INE2"] == 3

def test_invalid_negative_holdings():
    tx = [{"trade_date": "2024-01-01", "isin": "INE3", "buy_sell": "SELL", "quantity": 5}]
    import pytest
    with pytest.raises(ValueError):
        get_holdings(tx, "2024-01-15")
    assert check_negative_holdings(tx, "2024-01-15") == ["INE3"]

def test_opening_position():
    tx = [{"trade_date": "2024-01-01", "isin": "INE4", "buy_sell": "OPENING", "quantity": 50}]
    h = get_holdings(tx, "2024-01-15")
    assert h["INE4"] == 50

def test_external_cash_ignored():
    tx = [
        {"trade_date": "2024-01-01", "isin": "INE5", "buy_sell": "BUY", "quantity": 10},
        {"event_type": "cash", "trade_date": "2024-01-02", "isin": "INE5", "buy_sell": "BUY", "quantity": 999},
    ]
    h = get_holdings(tx, "2024-01-15")
    assert h["INE5"] == 10

def test_stock_quantity_and_history():
    q = get_stock_quantity(TX, "INE1", "2024-03-15")
    assert q == 8
    hist = get_transaction_history(TX, "INE1")
    assert len(hist) == 3

def test_symbol_aliases():
    assert get_symbol_aliases(TX) == {"INE1": ["ABC"]}

def test_transaction_ledger_class():
    ledger = TransactionLedger(TX)
    assert ledger.get_stock_quantity("INE1", "2024-03-15") == 8
    ledger.add({"trade_date": "2024-04-01", "isin": "INE1", "buy_sell": "SELL", "quantity": 8})
    assert "INE1" not in ledger.get_holdings("2024-04-15")

def test_settlement_basis():
    tx = [{"trade_date": "2024-01-01", "settlement_date": "2024-01-03", "isin": "INE6", "buy_sell": "BUY", "quantity": 5}]
    assert get_holdings(tx, "2024-01-02", date_basis="trade")["INE6"] == 5
    assert "INE6" not in get_holdings(tx, "2024-01-02", date_basis="settlement")
    assert get_holdings(tx, "2024-01-03", date_basis="settlement")["INE6"] == 5
