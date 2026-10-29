import os

from src.pdf_parser import parse_folder, parse_pdf

SAMPLE = "/Users/anmol/Downloads/TRX-Equity_10-07-2025_982490.pdf"


def test_parse_sample_pdf():
    rows = parse_pdf(SAMPLE)
    assert len(rows) > 0
    r = rows[0]
    assert r["isin"] and len(r["isin"]) == 12
    assert r["settlement_date"] is not None
    assert r["buy_sell"] in ("BUY", "SELL")
    assert r["quantity"] is not None
    assert r["net_amount"] is not None


def test_discover_and_parse_folder():
    folder = os.path.dirname(SAMPLE)
    rows = parse_folder(folder)
    # At least the sample file's rows
    assert len(rows) > 0
    # sorted by date
    dates = [r["settlement_date"] for r in rows if r["settlement_date"]]
    assert dates == sorted(dates)