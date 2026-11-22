"""Tests for on-demand price fetching and the XIRR price-lookup factory."""
from __future__ import annotations

import types
from datetime import date, timedelta
from typing import ClassVar

import pandas as pd
import pytest

from app.shared import price_lookup_factory
from market_data import prices, providers
from market_data.repository import MarketDataStore

TODAY = date.today()


def _fake_yf_module(series):
    """Minimal yfinance stand-in exposing ``Ticker(...).history(...)``."""

    class Ticker:
        calls: ClassVar[list[tuple]] = []

        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, start=None, end=None, auto_adjust=False, actions=True, timeout=30):
            Ticker.calls.append((self.symbol, start, end))
            idx = pd.to_datetime([d for d, _ in series])
            return pd.DataFrame(
                {
                    "Close": [p for _, p in series],
                    "Adj Close": [p for _, p in series],
                    "Dividends": [0.0 for _ in series],
                    "Stock Splits": [0.0 for _ in series],
                },
                index=idx,
            )

    module = types.ModuleType("yfinance")
    module.Ticker = Ticker
    return module, Ticker


def _seed_mapping(db, security_id="INE001A01036", symbol="ABC.NS"):
    MarketDataStore(db).upsert_security_symbol_map([
        {
            "security_id": security_id,
            "symbol": symbol,
            "provider_symbol": symbol,
            "provider": "yfinance",
            "verification_status": "verified",
        }
    ])


@pytest.fixture(autouse=True)
def _clear_price_cache():
    prices.reset_price_fetch_cache()
    yield
    prices.reset_price_fetch_cache()


@pytest.fixture
def fake_yf(monkeypatch):
    module, ticker = _fake_yf_module([("2015-06-01", 100.0), ("2015-06-02", 101.0)])
    monkeypatch.setattr(providers, "yfinance", module, raising=False)
    monkeypatch.setattr(providers, "yf", module, raising=False)
    return ticker


def test_fetch_stock_prices_without_symbol_mapping(tmp_path):
    db = str(tmp_path / "md.sqlite3")
    res = prices.fetch_stock_prices("INE999A01001", start="2015-01-01", end="2015-12-31", db_path=db)
    assert res["rows_fetched"] == 0
    assert "no_symbol_mapping" in res["errors"]


def test_fetch_stock_prices_persists_rows(tmp_path, fake_yf):
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db)
    res = prices.fetch_stock_prices("INE001A01036", start="2015-01-01", end="2015-12-31", db_path=db)
    assert res["symbol"] == "ABC.NS"
    assert res["rows_fetched"] == 2
    assert res["upserted"] == 2
    assert MarketDataStore(db).get_stock_price("INE001A01036", "2015-06-02") == 101.0


def test_fetch_stock_prices_can_skip_persistence(tmp_path, fake_yf):
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db)
    res = prices.fetch_stock_prices(
        "INE001A01036", start="2015-01-01", end="2015-12-31", db_path=db, persist=False
    )
    assert res["rows_fetched"] == 2
    assert res["upserted"] == 0
    assert res["prices"] == [("2015-06-01", 100.0), ("2015-06-02", 101.0)]
    assert MarketDataStore(db).get_stock_price("INE001A01036", "2015-06-02") is None


def test_get_stock_price_with_fallback_prefers_db(tmp_path, monkeypatch):
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db)
    MarketDataStore(db).upsert_stock_prices([{
        "security_id": "INE001A01036",
        "exchange": "NSE",
        "trade_date": "2015-06-01",
        "close_price": 55.0,
        "source": "manual",
    }])

    def _boom(*args, **kwargs):
        raise AssertionError("network fetch must not run when the DB has the price")

    monkeypatch.setattr(prices, "fetch_stock_prices", _boom)
    assert prices.get_stock_price_with_fallback(
        "INE001A01036", "2015-06-01", db_path=db, start="2015-01-01", end="2015-12-31"
    ) == 55.0


def test_get_stock_price_with_fallback_fetches_then_caches(tmp_path, fake_yf):
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db)
    price = prices.get_stock_price_with_fallback(
        "INE001A01036", "2015-06-02", db_path=db, start="2015-01-01", end="2015-12-31"
    )
    assert price == 101.0
    assert len(fake_yf.calls) == 1
    # A second lookup inside the same window is served from the in-process cache.
    assert prices.get_stock_price_with_fallback(
        "INE001A01036", "2015-06-01", db_path=db, start="2015-01-01", end="2015-12-31"
    ) == 100.0
    assert len(fake_yf.calls) == 1
    # ... and the fetch was persisted for later (offline) runs.
    assert MarketDataStore(db).get_stock_price("INE001A01036", "2015-06-01") == 100.0


def test_get_stock_price_with_fallback_respects_env_switch(tmp_path, fake_yf, monkeypatch):
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db)
    monkeypatch.setenv("ICICI_PRICE_FETCH_FALLBACK", "false")
    assert prices.get_stock_price_with_fallback(
        "INE001A01036", "2015-06-02", db_path=db, start="2015-01-01", end="2015-12-31"
    ) is None
    assert fake_yf.calls == []


def test_get_stock_price_with_fallback_never_fetches_future_dates(tmp_path, fake_yf):
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db)
    future = (TODAY + timedelta(days=30)).isoformat()
    assert prices.get_stock_price_with_fallback(
        "INE001A01036", future, db_path=db, start="2015-01-01", end=future
    ) is None
    assert fake_yf.calls == []


def _holdings_df(price=222.0):
    return pd.DataFrame([{
        "isin": "INE001A01036",
        "quantity": 10.0,
        "current_price": price,
        "current_value": price * 10.0,
    }])


def test_lookup_factory_prefers_db_without_fetching(tmp_path, monkeypatch):
    db = str(tmp_path / "md.sqlite3")
    MarketDataStore(db).upsert_stock_prices([{
        "security_id": "INE001A01036",
        "exchange": "NSE",
        "trade_date": "2015-06-01",
        "close_price": 42.0,
        "source": "manual",
    }])

    def _boom(*args, **kwargs):
        raise AssertionError("no fetch expected")

    monkeypatch.setattr(prices, "fetch_stock_prices", _boom)
    lookup = price_lookup_factory(
        market_db=db, start_date=date(2015, 1, 1), end_date=date(2015, 12, 31)
    )
    assert lookup("INE001A01036", date(2015, 6, 1)) == 42.0


def test_lookup_factory_fetches_missing_historical_price(tmp_path, fake_yf):
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db)
    lookup = price_lookup_factory(
        market_db=db,
        start_date=date(2015, 1, 1),
        end_date=date(2015, 12, 31),
        fetch_missing=True,
        max_fetch_symbols=5,
    )
    assert lookup("INE001A01036", date(2015, 6, 1)) == 100.0
    assert "INE001A01036" in lookup.fetched_securities


def test_lookup_factory_budget_limits_fetches(tmp_path, fake_yf):
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db, "INE001A01036", "ABC.NS")
    _seed_mapping(db, "INE002A01018", "XYZ.NS")
    lookup = price_lookup_factory(
        market_db=db,
        start_date=date(2015, 1, 1),
        end_date=date(2015, 12, 31),
        fetch_missing=True,
        max_fetch_symbols=1,
    )
    assert lookup("INE001A01036", date(2015, 6, 1)) == 100.0
    assert lookup("INE002A01018", date(2015, 6, 1)) is None
    assert lookup.skipped_securities == {"INE002A01018"}


def test_lookup_factory_ignores_distant_holdings_snapshot(tmp_path):
    db = str(tmp_path / "md.sqlite3")
    lookup = price_lookup_factory(
        market_db=db,
        start_date=date(2015, 1, 1),
        end_date=date(2015, 12, 31),
        holdings=_holdings_df(222.0),
        fetch_missing=False,
    )
    assert lookup("INE001A01036", date(2015, 12, 31)) is None


def test_lookup_factory_uses_recent_holdings_snapshot(tmp_path):
    db = str(tmp_path / "md.sqlite3")
    lookup = price_lookup_factory(
        market_db=db,
        start_date=TODAY - timedelta(days=365),
        end_date=TODAY,
        holdings=_holdings_df(222.0),
        fetch_missing=False,
    )
    assert lookup("INE001A01036", TODAY) == 222.0


def test_provider_chain_defaults_to_yfinance_then_http(monkeypatch):
    monkeypatch.delenv("ICICI_PRICE_PROVIDER", raising=False)
    assert [p.name for p in prices._provider_chain(None)] == ["yfinance", "yahoo_chart"]


def test_provider_chain_env_selection(monkeypatch):
    monkeypatch.setenv("ICICI_PRICE_PROVIDER", "yahoo_chart")
    assert [p.name for p in prices._provider_chain(None)] == ["yahoo_chart"]
    monkeypatch.setenv("ICICI_PRICE_PROVIDER", "yfinance")
    assert [p.name for p in prices._provider_chain(None)] == ["yfinance"]


def test_fetch_stock_prices_falls_back_when_first_provider_fails(tmp_path, monkeypatch):
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db)

    class _Failing:
        name = "yfinance"

        def fetch_prices(self, **kwargs):
            raise RuntimeError("429 rate limited")

    class _Working:
        name = "yahoo_chart"

        def fetch_prices(self, **kwargs):
            return [{"trade_date": "2015-06-01", "close_price": 77.0, "exchange": "NSE"}]

    monkeypatch.setattr(
        prices, "_provider_chain", lambda provider=None: [_Failing(), _Working()]
    )
    res = prices.fetch_stock_prices(
        "INE001A01036", start="2015-01-01", end="2015-12-31", db_path=db
    )
    assert res["source"] == "yahoo_chart"
    assert res["rows_fetched"] == 1
    assert res["upserted"] == 1
    assert any("429" in err for err in res["errors"])
    assert MarketDataStore(db).get_stock_price("INE001A01036", "2015-06-01") == 77.0


def _seed_many_prices(db, ticker, n=250, start="2023-01-01"):
    """Seed ``n`` price rows for ``ticker`` so it passes the _MIN_TRADING_DAYS check."""
    from datetime import date as _d
    from datetime import timedelta as _td
    rows = []
    d = _d.fromisoformat(start)
    for i in range(n):
        rows.append({
            "security_id": ticker,
            "exchange": "NSE",
            "trade_date": (d + _td(days=i)).isoformat(),
            "close_price": 100.0 + i,
            "source": "seed",
        })
    MarketDataStore(db).upsert_stock_prices(rows)


def test_ensure_price_history_skips_cached(tmp_path, monkeypatch):
    """ensure_price_history should skip tickers that already have sufficient history."""
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db, "INE001A01036", "ABC.NS")
    _seed_many_prices(db, "INE001A01036", n=250)  # >= _MIN_TRADING_DAYS

    fetched = []
    original_fetch = prices.fetch_stock_prices

    def _tracking_fetch(*args, **kwargs):
        fetched.append(args[0] if args else kwargs.get("security_id"))
        return original_fetch(*args, **kwargs)

    monkeypatch.setattr(prices, "fetch_stock_prices", _tracking_fetch)

    res = prices.ensure_price_history(["INE001A01036"], db_path=db)
    assert res["skipped"] == ["INE001A01036"]
    assert res["fetched"] == []
    assert fetched == []  # fetch_stock_prices must NOT be called for cached ticker


def test_ensure_price_history_fetches_missing(tmp_path, monkeypatch):
    """ensure_price_history should fetch only tickers missing from the DB."""
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db, "INE001A01036", "ABC.NS")
    _seed_mapping(db, "INE002A01018", "XYZ.NS")
    _seed_many_prices(db, "INE001A01036", n=250)  # cached
    # INE002A01018 has no rows -> should be fetched

    fetch_calls = []

    def _fake_fetch(security_id, start=None, end=None, db_path=None, **kwargs):
        fetch_calls.append(security_id)
        if security_id == "INE002A01018":
            return {
                "security_id": security_id,
                "symbol": "XYZ.NS",
                "rows_fetched": 5,
                "upserted": 5,
                "prices": [("2023-01-01", 100.0), ("2023-01-02", 101.0),
                           ("2023-01-03", 102.0), ("2023-01-04", 103.0),
                           ("2023-01-05", 104.0)],
                "errors": [],
            }
        return {"security_id": security_id, "rows_fetched": 0, "prices": [], "errors": ["no rows"]}

    monkeypatch.setattr(prices, "fetch_stock_prices", _fake_fetch)

    res = prices.ensure_price_history(
        ["INE001A01036", "INE002A01018"], db_path=db
    )
    assert res["skipped"] == ["INE001A01036"]
    assert res["fetched"] == ["INE002A01018"]
    assert fetch_calls == ["INE002A01018"]  # only missing ticker was fetched


def test_ensure_price_history_tracks_failures(tmp_path, monkeypatch):
    """ensure_price_history should record failed tickers and not retry them."""
    db = str(tmp_path / "md.sqlite3")
    _seed_mapping(db, "INE001A01036", "ABC.NS")
    # INE001A01036 has no price rows but symbol mapping exists

    def _boom_fetch(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(prices, "fetch_stock_prices", _boom_fetch)
    prices._FETCH_FAILED_TICKERS.clear()

    res1 = prices.ensure_price_history(["INE001A01036"], db_path=db)
    assert res1["failed"] == ["INE001A01036"]
    assert "INE001A01036" in prices._FETCH_FAILED_TICKERS

    # Second call should not retry (ticker is in failed set)
    fetch_calls = []
    def _tracking_fetch(*args, **kwargs):
        fetch_calls.append(args[0] if args else kwargs.get("security_id"))
        return {"security_id": args[0] if args else kwargs.get("security_id"),
                "rows_fetched": 0, "prices": [], "errors": []}

    monkeypatch.setattr(prices, "fetch_stock_prices", _tracking_fetch)
    res2 = prices.ensure_price_history(["INE001A01036"], db_path=db)
    assert res2["failed"] == ["INE001A01036"]
    assert fetch_calls == []  # not retried
