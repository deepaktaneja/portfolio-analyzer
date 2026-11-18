"""Tests for the ICICI XLS transaction importer."""
from __future__ import annotations

from importers.xls.icici_transactions import parse_transactions_xls

HEADER = "Stock Symbol\tCompany Name\tISIN Code\tAction\tQuantity\tTransaction Price\tBrokerage\tTransaction Charges\tStampDuty\tSegment\tSTT Paid/Not Paid\tRemarks\tTransaction Date\tExchange"


def _write(tmp_path, lines):
    p = tmp_path / "tx.xls"
    p.write_text("\n".join([HEADER] + lines), encoding="utf-8")
    return str(p)


def test_parse_buy_and_sell(tmp_path):
    path = _write(tmp_path, [
        "APOHOS\tAPOLLO HOSPITALS\tINE437A01024\tBuy\t2\t100.00\t2.00\t0.50\t1.00\tTT\tSTT Paid\ticicidirect\t09-Feb-2026\tNSE",
        "APOHOS\tAPOLLO HOSPITALS\tINE437A01024\tSell\t1\t150.00\t1.50\t0.25\t0.00\tTT\tSTT Paid\ticicidirect\t10-Mar-2026\tNSE",
    ])
    rows = parse_transactions_xls(path)
    assert len(rows) == 2
    buy, sell = rows
    assert buy["buy_sell"] == "BUY" and buy["isin"] == "INE437A01024"
    assert buy["trade_date"] == "2026-02-09"
    assert buy["gross_amount"] == 200.0
    assert round(buy["net_amount"], 2) == 203.5
    assert sell["buy_sell"] == "SELL"
    assert round(sell["net_amount"], 2) == 148.25


def test_skips_invalid_isin(tmp_path):
    path = _write(tmp_path, [
        "BAD\tBAD CO\tNOTANISIN\tBuy\t1\t10\t0\t0\t0\tTT\tSTT Paid\tx\t09-Feb-2026\tNSE",
        "APOHOS\tAPOLLO HOSPITALS\tINE437A01024\tBuy\t1\t10\t0\t0\t0\tTT\tSTT Paid\tx\t09-Feb-2026\tNSE",
    ])
    rows = parse_transactions_xls(path)
    assert len(rows) == 1


def test_zero_price_bonus_row_kept(tmp_path):
    path = _write(tmp_path, [
        "BAJFI\tBAJAJ FINANCE\tINE296A01032\tBuy\t872\t0.00\t0\t0\t0\tRolling\tSTT Paid\tSplit and bonus\t18-Jun-2025\tNSE",
    ])
    rows = parse_transactions_xls(path)
    assert len(rows) == 1
    assert rows[0]["net_amount"] == 0.0
    assert rows[0]["trade_date"] == "2025-06-18"


def test_unrecognised_file_raises(tmp_path):
    p = tmp_path / "bad.xls"
    p.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    try:
        parse_transactions_xls(str(p))
    except ValueError:
        return
    raise AssertionError("expected ValueError")
