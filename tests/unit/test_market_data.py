
import pandas as pd

from market_data.adapters import import_index_tri, import_stock_prices
from market_data.prices import (
    get_index_tri,
    get_market_data_quality_report,
    get_missing_prices,
    get_stock_price,
    get_stock_prices,
)
from market_data.repository import MarketDataStore


def test_stock_import_and_retrieve(tmp_path):
    rows = [
        {"date": "2024-01-01", "symbol": "ABC", "close": 100.0},
        {"date": "2024-01-02", "symbol": "ABC", "close": 101.0},
        {"date": "2024-01-03", "symbol": "ABC", "close": 0.0},
    ]
    p = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    parsed, _ = import_stock_prices(str(p), exchange="NSE", source="test")
    assert len(parsed) == 3
    db = str(tmp_path / "md.db")
    store = MarketDataStore(db)
    store.upsert_stock_prices(parsed)
    assert get_stock_price("ABC", "2024-01-02", db_path=db) == 101.0
    assert get_stock_prices("ABC", "2024-01-01", "2024-01-02", db_path=db) == [("2024-01-01", 100.0), ("2024-01-02", 101.0)]
    assert get_stock_price("ABC", "2024-01-04", db_path=db) == 0.0
    assert get_stock_price("ABC", "2024-01-04", db_path=db, allow_last=False) is None


def test_index_tri_import(tmp_path):
    rows = [{
        "date": "2024-01-01", "level": 1000.0},
        {"date": "2024-01-02", "level": 1005.0},
    ]
    p = tmp_path / "tri.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    parsed, _ = import_index_tri(str(p), "NIFTY50TRI", source="test")
    assert len(parsed) == 2
    db = str(tmp_path / "md.db")
    from market_data.repository import MarketDataStore
    store = MarketDataStore(db)
    store.upsert_index_tri(parsed)
    assert get_index_tri("NIFTY50TRI", "2024-01-01", db_path=db) == 1000.0
    assert get_index_tri("NIFTY50TRI", "2024-01-03", db_path=db) == 1005.0

def test_incremental_idempotency(tmp_path):
    rows = [{"date": "2024-01-01", "symbol": "ABC", "close": 100.0}]
    p = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    parsed, _ = import_stock_prices(str(p))
    db = str(tmp_path / "md.db")
    from market_data.repository import MarketDataStore
    store = MarketDataStore(db)
    store.upsert_stock_prices(parsed)
    store.upsert_stock_prices(parsed)
    assert get_stock_prices("ABC", "2024-01-01", "2024-01-01", db_path=db) == [("2024-01-01", 100.0)]


def test_missing_prices(tmp_path):
    rows = [{"date": "2024-01-01", "symbol": "ABC", "close": 100.0}]
    p = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    parsed, _ = import_stock_prices(str(p))
    db = str(tmp_path / "md.db")
    from market_data.repository import MarketDataStore
    store = MarketDataStore(db)
    store.upsert_stock_prices(parsed)
    missing = get_missing_prices("2024-01-01", "2024-01-05", db_path=db, security_ids=["ABC"])
    assert len(missing) == 4

def test_quality_report(tmp_path):
    rows = [
        {"date": "2024-01-01", "symbol": "ABC", "close": 100.0},
        {"date": "2024-01-02", "symbol": "ABC", "close": 0.0},
        {"date": "2024-01-03", "symbol": "ABC", "close": 200.0},
    ]
    p = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    parsed, _ = import_stock_prices(str(p))
    db = str(tmp_path / "md.db")
    from market_data.repository import MarketDataStore
    store = MarketDataStore(db)
    store.upsert_stock_prices(parsed)
    report = get_market_data_quality_report(db_path=db)
    assert "ABC" in report["securities"]
    types = {a["type"] for a in report["anomalies"]}
    assert "non_positive_price" in types
    assert "large_move" in types

def test_xlsx_import(tmp_path):
    rows = [{"date": "2024-01-01", "symbol": "XYZ", "close": 50.0}]
    p = tmp_path / "prices.xlsx"
    pd.DataFrame(rows).to_excel(p, index=False)
    parsed, _ = import_stock_prices(str(p))
    assert len(parsed) == 1
    assert parsed[0]["security_id"] == "XYZ"

