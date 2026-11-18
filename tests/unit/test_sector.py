"""Tests for sector enrichment and manual symbol mapping."""
from __future__ import annotations

import pandas as pd

from market_data import sector


class _FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol
        self.info = {"sector": "Energy", "industry": "Oil & Gas"}


class _FakeYF:
    Ticker = _FakeTicker


def _use_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(sector, "XIRR_DB", str(tmp_path / "x.sqlite3"))
    monkeypatch.setattr(sector, "MARKET_DB", str(tmp_path / "m.sqlite3"))


def test_symbol_mapping_roundtrip(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    saved = sector.upsert_symbol_mapping("INE1", "TESTCO", "NSE")
    assert saved == "TESTCO.NS"
    maps = sector.get_symbol_mappings(["INE1"])
    assert maps["INE1"]["symbol"] == "TESTCO.NS"


def test_symbol_mapping_bse_suffix(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    assert sector.upsert_symbol_mapping("INE2", "TESTCO", "BSE") == "TESTCO.BO"


def test_sector_cache_roundtrip(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    n = sector.upsert_sector_mapping({"INE1": {"sector": "Tech", "industry": "Software", "symbol": "X.NS", "exchange": "NSE", "provider": "yfinance"}})
    assert n == 1
    cached = sector.get_sector_mapping(["INE1"])
    assert cached["INE1"]["sector"] == "Tech"


def test_enrich_sectors_uses_mapping(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(sector, "yfinance", _FakeYF)
    monkeypatch.setattr(sector, "get_symbol_mappings", lambda isins: {"INE1": {"symbol": "TESTCO.NS", "exchange": "NSE"}})
    res = sector.enrich_sectors_for_isins(["INE1"], delay=0)
    assert res["INE1"]["sector"] == "Energy"


def test_enrich_sectors_without_mapping(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(sector, "get_symbol_mappings", lambda isins: {})
    res = sector.enrich_sectors_for_isins(["INE9"], delay=0)
    assert res["INE9"]["error"] == "no_symbol_mapping"


def test_enrich_holdings_fills_columns(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    sector.upsert_sector_mapping({"INE1": {"sector": "Tech", "industry": "Software"}})
    df = pd.DataFrame([{"isin": "INE1", "symbol": "X", "quantity": 1}, {"isin": "INE2", "symbol": "Y", "quantity": 2}])
    sector.enrich_holdings_with_sectors(df)
    assert df.loc[0, "sector"] == "Tech"
    assert df.loc[0, "industry"] == "Software"
    assert pd.isna(df.loc[1, "sector"])


def test_manual_sector_override(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    sector.upsert_sector_override("INE9", "Financial Services", "Finance")
    cached = sector.get_sector_mapping(["INE9"])
    assert cached["INE9"]["sector"] == "Financial Services"
    assert cached["INE9"]["provider"] == "manual"
    df = pd.DataFrame([{"isin": "INE9", "symbol": "X", "quantity": 1}])
    sector.enrich_holdings_with_sectors(df)
    assert df.loc[0, "sector"] == "Financial Services"
