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


def _orderbook_csv(tmp_path, lines):
    """Write an ICICI order-book CSV with UTF-8 BOM and exact headers."""
    header = (
        "Date,Stock,Action,Qty,Price,Trade Value,Order Ref.,Settlement,Segment,"
        "DP Id - Client DP Id,Exchange,STT,Transaction and SEBI Turnover charges,"
        "Stamp Duty,Brokerage + Service Tax,Brokerage Incl. Taxes"
    )
    p = tmp_path / "orderBook_Equity.csv"
    p.write_text("\n".join([header] + lines), encoding="utf-8-sig")
    return str(p)


def test_parse_orderbook_buy_and_sell(tmp_path):
    path = _orderbook_csv(tmp_path, [
        '"12-Nov-26","TIPSMUSIC","Buy","6","791","4746","20261112Z500013433","2026214","Rolling T1","IN302679-33581099","NSE","4.75","0.15","0.71","14.03","19.64"',
        '"06-Nov-26","AXISBANK","Sell","30","1302.15","39064.6","20261106Z500036997","2026210","Rolling T1","IN302679-33581099","NSE","39","1.24","0","115.46","155.7"',
    ])
    rows = parse_transactions_xls(path)
    assert len(rows) == 2
    buy, sell = rows
    assert buy["buy_sell"] == "BUY"
    assert buy["isin"] is None
    assert buy["security"] == "TIPSMUSIC.NS"
    assert buy["trade_date"] == "2026-11-12"
    assert buy["settlement_date"] == "2026-11-12"
    assert buy["quantity"] == 6.0
    assert buy["gross_rate"] == 791.0
    assert buy["gross_amount"] == 4746.0
    assert buy["order_no"] == "20261112Z500013433"
    assert round(buy["net_amount"], 2) == 4746.0 + 19.64  # gross + brokerage incl. taxes
    assert sell["buy_sell"] == "SELL"
    assert sell["security"] == "AXISBANK.NS"
    assert sell["trade_date"] == "2026-11-06"
    assert sell["quantity"] == 30.0
    assert sell["order_no"] == "20261106Z500036997"
    assert round(sell["net_amount"], 2) == round(30.0 * 1302.15 - 155.7, 2)  # gross - fees


def test_orderbook_skips_non_executed_rows(tmp_path):
    path = _orderbook_csv(tmp_path, [
        '"12-Nov-26","TIPSMUSIC","Cancelled","6","791","4746","REF1","2026214","Rolling T1","IN302679-33581099","NSE","0","0","0","0","0"',
        '"11-Nov-26","LT","Rejected","2","4196.5","8393","REF2","2026214","Rolling T1","IN302679-33581099","NSE","0","0","0","0","0"',
        '"10-Nov-26","APOLLOHOSP","Pending","1","8535.5","8535.5","REF3","2026212","Rolling T1","IN302679-33581099","NSE","0","0","0","0","0"',
        '"09-Nov-26","BAJFINANCE","Open","10","972.6","9726","REF4","2026207","Rolling T1","IN302679-33581099","NSE","0","0","0","0","0"',
        '"08-Nov-26","APTUS","Buy","0","198.03","0","REF5","2026202","Rolling T1","IN302679-33581099","NSE","0","0","0","0","0"',
        '"07-Nov-26","TECHM","Buy","5","400","2000","REF6","2026201","Rolling T1","IN302679-33581099","NSE","0","0","0","0","0"',
    ])
    rows = parse_transactions_xls(path)
    # Only the last row (valid Buy) should be kept
    assert len(rows) == 1
    assert rows[0]["buy_sell"] == "BUY"
    assert rows[0]["security"] == "TECHM.NS"
    assert rows[0]["trade_date"] == "2026-11-07"
    assert rows[0]["quantity"] == 5.0
    assert rows[0]["order_no"] == "REF6"


def test_orderbook_is_none_and_security_keyed(tmp_path):
    """isin is None and security carries the ticker; dedup key falls back to security."""
    path = _orderbook_csv(tmp_path, [
        '"12-Nov-26","TIPSMUSIC","Buy","6","791","4746","REF1","2026214","Rolling T1","IN302679-33581099","NSE","0","0","0","0","0"',
    ])
    rows = parse_transactions_xls(path)
    assert len(rows) == 1
    assert rows[0]["isin"] is None
    assert rows[0]["security"] == "TIPSMUSIC.NS"
