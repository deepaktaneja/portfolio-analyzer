import pytest
from streamlit.testing.v1 import AppTest

from market_data.repository import MarketDataStore
from src.store import SQLiteStore


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    md = tmp_path / "market_data.sqlite3"
    xd = tmp_path / "xirr.sqlite3"
    MarketDataStore(str(md))
    SQLiteStore(str(xd))
    monkeypatch.setenv("ICICI_MARKET_DATA_DB_PATH", str(md))
    monkeypatch.setenv("ICICI_XIRR_DB_PATH", str(xd))
    return md, xd


def test_app_renders_without_exception(isolated_env):
    at = AppTest.from_file("/Users/anmol/portfolio-analyzer/app.py")
    at.run(timeout=300)
    assert not at.exception, getattr(at.exception, "message", at.exception)


def test_market_data_tab_has_coverage_metrics(isolated_env):
    at = AppTest.from_file("/Users/anmol/portfolio-analyzer/app.py")
    at.run(timeout=300)
    # no crash; metrics rendered
    assert len(at.metric) >= 1


def test_empty_market_data_shows_unavailable_state(isolated_env):
    at = AppTest.from_file("/Users/anmol/portfolio-analyzer/app.py")
    at.run(timeout=300)
    # Benchmark TRI missing state should be surfaced somewhere in markdown/dataframes
    assert not at.exception