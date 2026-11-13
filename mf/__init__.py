"""Mutual fund ingestion, NAV & XIRR engine (Stage 3/4/5)."""

from models import (
    MFScheme,
    MFPlan,
    MFNav,
    MFTransaction,
    MFPortfolio,
)

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

__all__ = [
    "MFScheme",
    "MFPlan",
    "MFNav",
    "MFTransaction",
    "MFPortfolio",
    "load_transaction_history",
    "get_transaction_history",
    "simulate_stock_replacement",
    "calculate_backtest_xirr",
    "calculate_terminal_value",
    "calculate_realized_unrealized_gains",
    "compare_portfolios",
    "calculate_portfolio_mf_xirr",
    "ReplacementResult",
]
