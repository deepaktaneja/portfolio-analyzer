"""High-level market data API (Stage 04)."""
from __future__ import annotations

from market_data.repository import MarketDataStore


def _store(db_path=None):
    if db_path is None:
        db_path = "data/market_data.sqlite3"
    return MarketDataStore(db_path)

def get_stock_price(security_id, date, db_path=None, exchange=None, allow_last=True):
    return _store(db_path).get_stock_price(security_id, date, exchange=exchange, allow_last=allow_last)

def get_stock_prices(security_id, start, end, db_path=None, exchange=None):
    return _store(db_path).get_stock_prices(security_id, start, end, exchange=exchange)

def get_index_tri(index_id, date, db_path=None, allow_last=True):
    return _store(db_path).get_index_tri(index_id, date, allow_last=allow_last)

def get_missing_prices(start, end, db_path=None, security_ids=None):
    return _store(db_path).get_missing_prices(start, end, security_ids=security_ids)

def get_market_data_quality_report(db_path=None):
    return _store(db_path).get_market_data_quality_report()
