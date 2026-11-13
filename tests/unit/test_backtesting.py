"""Automated tests for MF/Equity backtesting engine (Stage 5)."""

from decimal import Decimal
from mf.backtesting import (
    load_transaction_history,
    get_transaction_history,
    simulate_stock_replacement,
    calculate_backtest_xirr,
    calculate_terminal_value,
    calculate_realized_unrealized_gains,
    compare_portfolios,
    calculate_portfolio_mf_xirr,
    ReplacementResult,
)


def test_load_transaction_history_filters_by_security():
    txs = [
        {"isin": "INE1", "security": "INE1", "trade_date": "2024-01-01", "side": "BUY", "amount": 1000},
        {"isin": "INE2", "security": "INE2", "trade_date": "2024-01-02", "side": "BUY", "amount": 2000},
        {"isin": "INE1", "security": "INE1", "trade_date": "2024-06-01", "side": "SELL", "amount": 1200},
    ]
    result = load_transaction_history(txs, "INE1")
    assert len(result) == 2


def test_load_transaction_history_empty():
    result = load_transaction_history([], "INE1")
    assert result == []


def test_simulate_stock_replacement_full():
    txs = [
        {"isin": "INE1", "security": "INE1", "trade_date": "2024-01-01", "side": "BUY", "amount": 1000, "units": 10},
        {"isin": "INE1", "security": "INE1", "trade_date": "2024-02-01", "side": "BUY", "amount": 1100, "units": 11},
        {"isin": "INE2", "security": "INE2", "trade_date": "2024-03-01", "side": "BUY", "amount": 500, "units": 5},
    ]
    result = simulate_stock_replacement(txs, "INE1", "INE3", "2024-12-01", is_full_replacement=True)
    assert result.replacement_info["replaced_count"] == 2
    assert result.replacement_info["preserved_count"] == 1


def test_simulate_stock_replacement_partial():
    txs = [
        {"isin": "INE1", "security": "INE1", "trade_date": "2024-01-01", "side": "BUY", "amount": 1000, "units": 10},
        {"isin": "INE1", "security": "INE1", "trade_date": "2024-02-01", "side": "BUY", "amount": 1100, "units": 11},
    ]
    result = simulate_stock_replacement(txs, "INE1", "INE3", "2024-12-01", is_full_replacement=False, selected_transaction_indices=[0])
    assert result.replacement_info["replaced_count"] == 1


def test_simulate_stock_replacement_no_transactions():
    result = simulate_stock_replacement([], "INE1", "INE3", "2024-12-01")
    assert "error" in result.replacement_info


def test_calculate_backtest_xirr_simple_buy_sell():
    def price_lookup(sec_id, d):
        return 120.0

    txs = [
        {"isin": "INE1", "security": "INE1", "trade_date": "2024-01-01", "side": "BUY", "amount": 1000, "units": 10},
        {"isin": "INE1", "security": "INE1", "trade_date": "2025-01-01", "side": "SELL", "amount": 1200, "units": 10},
    ]
    result = calculate_backtest_xirr(txs, "2025-01-01", price_lookup)
    assert result["reason"] is None
    assert result["xirr"] is not None


def test_calculate_backtest_xirr_insufficient_flows():
    def price_lookup(sec_id, d):
        return 100.0

    txs = [{"isin": "INE1", "security": "INE1", "trade_date": "2024-01-01", "side": "BUY", "amount": 1000}]
    result = calculate_backtest_xirr(txs, "2025-01-01", price_lookup)
    assert result["reason"] in ("insufficient_flows", "missing_opposite_sign")


def test_compare_portfolios_same_stock():
    def price_lookup(sec_id, d):
        return 120.0

    nav_data = {"INE1": Decimal("120")}

    original = [
        {"isin": "INE1", "security": "INE1", "trade_date": "2024-01-01", "side": "BUY", "amount": 1000, "units": 10},
        {"isin": "INE1", "security": "INE1", "trade_date": "2025-01-01", "side": "SELL", "amount": 1200, "units": 10},
    ]

    result = compare_portfolios(original, original, "2025-01-01", price_lookup, nav_data)
    comp = result["comparison"]
    assert comp["original_xirr"] == comp["hypothetical_xirr"]
    assert comp["xirr_difference"] == 0


def test_calculate_terminal_value():
    txs = [
        {"isin": "INE1", "security": "INE1", "trade_date": "2024-01-01", "side": "BUY", "amount": 1000, "units": 10},
    ]
    nav_data = {"INE1": Decimal("120")}
    result = calculate_terminal_value(txs, nav_data)
    assert result["INE1"] == Decimal("1200")
