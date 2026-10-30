import os
import shutil
from pathlib import Path

import pytest

from src.pdf_parser import parse_folder, parse_pdf
from src.store import SQLiteStore

SAMPLE = "/Users/anmol/Downloads/TRX-Equity_10-07-2025_982490.pdf"


def _skip_if_missing():
    if not Path(SAMPLE).exists():
        pytest.skip(f"Sample PDF not found: {SAMPLE}")


def test_incremental_parse_skips_cached(tmp_path):
    _skip_if_missing()
    folder = tmp_path / "stmts"
    folder.mkdir()
    shutil.copy(SAMPLE, folder / "TRX-Equity_10-07-2025_982490.pdf")
    db = tmp_path / "t.db"
    store = SQLiteStore(db)
    rows1 = parse_folder(str(folder), store=store)
    assert len(rows1) > 0
    n_files1 = len(store.known_files())
    # second run: no new files parsed
    rows2 = parse_folder(str(folder), store=store)
    assert len(rows2) == len(rows1)
    assert len(store.known_files()) == n_files1


def test_parse_sample_pdf():
    _skip_if_missing()
    rows = parse_pdf(SAMPLE)
    assert len(rows) > 0
    r = rows[0]
    assert r["isin"] and len(r["isin"]) == 12
    assert r["settlement_date"] is not None
    assert r["buy_sell"] in ("BUY", "SELL")
    assert r["quantity"] is not None
    assert r["net_amount"] is not None


def test_discover_and_parse_folder():
    _skip_if_missing()
    folder = os.path.dirname(SAMPLE)
    rows = parse_folder(folder)
    # At least the sample file's rows
    assert len(rows) > 0
    # sorted by date
    dates = [r["settlement_date"] for r in rows if r["settlement_date"]]
    assert dates == sorted(dates)