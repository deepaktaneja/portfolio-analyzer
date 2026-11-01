import os
import sys
import tempfile
import types
from unittest.mock import patch

import pandas as pd

from market_data.fetcher import sync_all_market_data
from market_data.repository import MarketDataStore


def _fake_yfinance():
    m = types.ModuleType("yfinance")

    class Ticker:
        def __init__(self, sym):
            self.sym = sym

        def history(self, start=None, end=None, auto_adjust=False, actions=True, timeout=30):
            idx = pd.to_datetime(["2024-01-01", "2024-01-02"])
            return pd.DataFrame(
                {"Close": [100.0, 101.0], "Adj Close": [99.0, 100.0], "Dividends": [0.0, 2.0], "Stock Splits": [0.0, 2.0]},
                index=idx,
            )

        @property
        def dividends(self):
            return pd.Series([2.0], index=pd.to_datetime(["2024-01-02"]))

        @property
        def splits(self):
            return pd.Series([2.0], index=pd.to_datetime(["2024-01-02"]))

    m.Ticker = Ticker
    return m


def test_sync_all_market_data_mocked():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "md.db")
        fake = _fake_yfinance()
        with patch.dict(sys.modules, {"yfinance": fake}):
            # reload providers module reference
            import market_data.providers as p
            p.yfinance = fake
            p.yf = fake
            r = sync_all_market_data(db_path=db, provider="yfinance", security_ids=["ABC"], symbols={"ABC": "ABC.NS"}, start="2024-01-01", end="2024-01-02")
        assert r["prices_upserted"] >= 2
        assert r["errors"] == []
        store = MarketDataStore(db)
        assert store.get_stock_price("ABC", "2024-01-01") == 100.0
        assert len(store.get_corporate_actions("ABC")) >= 1
        assert len(store.get_dividend_events("ABC")) >= 1


def test_provider_unavailable():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "md.db")
        with patch.dict(sys.modules, {"yfinance": None}):
            import market_data.providers as p
            p.yfinance = None
            p.yf = None
            r = sync_all_market_data(db_path=db, provider="yfinance", security_ids=["ABC"], symbols={"ABC": "ABC.NS"}, start="2024-01-01", end="2024-01-02")
        assert r["errors"]
