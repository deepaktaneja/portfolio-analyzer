import os

import pytest

os.environ.setdefault("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3")


def test_holdings_service_import():
    import ledger.holdings_service
    assert hasattr(ledger.holdings_service, "get_holdings")


def test_market_data_page_imports():
    import market_data.prices
    import market_data.repository
    r = market_data.prices.get_market_data_quality_report(db_path="data/market_data.sqlite3")
    assert "securities" in r


def test_app_syntax():
    import ast
    ast.parse(open("app.py").read())
