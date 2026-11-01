import os
import tempfile

from market_data.prices import (
    get_benchmark_sync_status,
    get_missing_benchmark_dates,
    sync_benchmark_history,
)
from market_data.repository import MarketDataStore


def test_benchmark_cache_roundtrip(tmp_path):
    db = str(tmp_path / "md.db")
    MarketDataStore(db)
    # seed one record via sync with a local CSV
    import pandas as pd
    p = tmp_path / "tri.csv"
    pd.DataFrame([{"date": "2024-01-01", "level": 1000.0}, {"date": "2024-01-02", "level": 1005.0}]).to_csv(p, index=False)
    res = sync_benchmark_history("NIFTY50TRI", "2024-01-01", "2024-01-02", db_path=db, path=str(p))
    assert res["upserted"] >= 1
    status = get_benchmark_sync_status("NIFTY50TRI", db_path=db)
    assert status["records"] >= 1
    assert status["earliest"] == "2024-01-01"
    missing = get_missing_benchmark_dates("NIFTY50TRI", "2024-01-01", "2024-01-02", db_path=db)
    assert missing == []


def test_missing_benchmark_dates(tmp_path):
    db = str(tmp_path / "md.db")
    MarketDataStore(db)
    missing = get_missing_benchmark_dates("NIFTY50TRI", "2024-01-01", "2024-01-02", db_path=db)
    assert "2024-01-01" in missing