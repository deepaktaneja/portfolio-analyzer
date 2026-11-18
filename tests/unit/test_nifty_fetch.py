"""Tests for the NIFTY TRI HTTP fetch helper."""
from __future__ import annotations

import market_data.fetcher as fetcher


class _Resp:
    def raise_for_status(self):
        return None

    def json(self):
        return {"d": "[{\"date\": \"01-Jan-2024\", \"level\": 100.0}]"}


class _SessionOk:
    def get(self, *a, **k):
        return None

    def post(self, *a, **k):
        return _Resp()


class _SessionBad:
    def get(self, *a, **k):
        return None

    def post(self, *a, **k):
        raise RuntimeError("blocked")


def test_fetch_nifty_tri_content_success(monkeypatch):
    monkeypatch.setattr("requests.Session", lambda: _SessionOk())
    out = fetcher._fetch_nifty_tri_content("NIFTY 50", "01-Jan-2024", "02-Jan-2024", retries=1, delay=0)
    assert "level" in out


def test_fetch_nifty_tri_content_raises(monkeypatch):
    monkeypatch.setattr("requests.Session", lambda: _SessionBad())
    try:
        fetcher._fetch_nifty_tri_content("NIFTY 50", "01-Jan-2024", "02-Jan-2024", retries=1, delay=0)
    except ValueError as exc:
        assert "failed" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")


def test_parse_nse_index_close_csv():
    text = "Index Name,Closing Index Value\nNifty 50,\"21,741.90\"\nNifty 500,\"19,429.15\"\n"
    assert fetcher._parse_nse_index_close_csv(text, "Nifty 50") == 21741.90


def test_sync_index_tri_nse_archive_upserts(tmp_path, monkeypatch):
    db = str(tmp_path / "m.sqlite3")
    from market_data.repository import MarketDataStore
    MarketDataStore(db)
    monkeypatch.setattr(
        "market_data.prices.get_missing_benchmark_dates",
        lambda *a, **k: ["2024-01-01", "2024-01-02"],
    )
    monkeypatch.setattr(
        fetcher,
        "_fetch_nse_index_close",
        lambda d, n, timeout=15: 100.0 if d.endswith("01") else 101.0,
    )
    res = fetcher.sync_index_tri_nse_archive(
        "NIFTY50TRI", "2024-01-01", "2024-01-02", db_path=db, max_requests=10
    )
    assert res["upserted"] == 2
    from market_data.prices import get_index_tri
    assert get_index_tri("NIFTY50TRI", "2024-01-01", db_path=db) == 100.0
