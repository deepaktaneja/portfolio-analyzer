"""Verify the refactored 5-page navigation structure.

Ensures exactly 5 top-level pages exist and every page renders without
exceptions (using an isolated empty data environment for speed). Also confirms
the reusable page/component modules import cleanly.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from market_data.repository import MarketDataStore
from src.store import SQLiteStore

EXPECTED_PAGES = [
    "1 · Portfolio",
    "2 · Direct Equity",
    "3 · Mutual Funds",
    "4 · Backtesting",
    "5 · Data & Settings",
]


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    md = tmp_path / "market_data.sqlite3"
    xd = tmp_path / "xirr.sqlite3"
    MarketDataStore(str(md))
    SQLiteStore(str(xd))
    monkeypatch.setenv("ICICI_MARKET_DATA_DB_PATH", str(md))
    monkeypatch.setenv("ICICI_XIRR_DB_PATH", str(xd))
    return md, xd


def test_page_modules_import():
    from app.pages import backtesting, data_settings, holdings_research, performance, portfolio
    for mod in (portfolio, performance, holdings_research, backtesting, data_settings):
        assert hasattr(mod, "render"), f"{mod.__name__} missing render()"


def test_components_import():
    from app.components import filters, ui
    assert hasattr(filters, "render_sidebar_filters")
    for fn in ("section", "metric_row", "safe_dataframe", "data_freshness_caption", "estimated_xirr_note"):
        assert hasattr(ui, fn)


def test_exactly_five_pages(isolated_env):
    at = AppTest.from_file("/Users/anmol/portfolio-analyzer/app.py", default_timeout=120)
    at.run()
    nav = None
    for r in at.sidebar.radio:
        if r.label == "Page":
            nav = r
            break
    assert nav is not None, "Page navigation radio missing"
    assert list(nav.options) == EXPECTED_PAGES
    assert len(EXPECTED_PAGES) == 5


def test_each_page_renders(isolated_env):
    for page in EXPECTED_PAGES:
        at = AppTest.from_file("/Users/anmol/portfolio-analyzer/app.py", default_timeout=120)
        at.run()
        for r in at.sidebar.radio:
            if r.label == "Page":
                r.set_value(page).run()
                break
        assert not at.exception, f"{page} raised: {getattr(at.exception, 'message', at.exception)}"


def test_asset_type_filter_present(isolated_env):
    at = AppTest.from_file("/Users/anmol/portfolio-analyzer/app.py", default_timeout=120)
    at.run()
    labels = [r.label for r in at.sidebar.radio]
    assert "Asset type" in labels, f"Asset type filter missing; radios={labels}"
