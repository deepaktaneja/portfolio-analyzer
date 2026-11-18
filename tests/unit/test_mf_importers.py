"""Tests for AMC MF statement importers (transaction + current valuation)."""

from mf.importers import parse_current_valuation, parse_mf_statement


def test_parse_amc_transaction_statement(tmp_path):
    p = tmp_path / "tx.csv"
    p.write_text(
        "MF_NAME,INVESTOR_NAME,PAN,FOLIO_NUMBER,PRODUCT_CODE,SCHEME_NAME,Type,"
        "TRADE_DATE,TRANSACTION_TYPE,DIVIDEND_RATE,AMOUNT,UNITS,PRICE,BROKER\n"
        "ABSL,X,ABC,123,B292G,Focused,Equity,27-SEP-2006,Purchase - SIP,,2000,138.334,14.4578,B1\n"
        "ABSL,X,ABC,123,B292G,Focused,Equity,28-SEP-2006,Registration of Nominee,,0,,,B1\n"
        "ABSL,X,ABC,123,B292G,Focused,Equity,01-JUN-2020,IDCW Payout,,4156.49,0,,B1\n"
        "ABSL,X,ABC,123,B292G,Focused,Equity,10-NOV-2006,Redemption,,1000,50,20,B1\n",
        encoding="utf-8",
    )
    rows, warns = parse_mf_statement(str(p))
    assert warns == []
    assert [r["side"] for r in rows] == ["SIP", "IDCW", "REDEMPTION"]  # nominee row skipped
    assert rows[0]["trade_date"] == "2006-09-27"      # dd-Mon-yyyy -> ISO
    assert rows[0]["scheme_code"] == "B292G"          # PRODUCT_CODE, not folio/type
    assert rows[0]["units"] == 138.334
    assert rows[1]["units"] == 0.0 and rows[1]["amount"] == 4156.49  # IDCW cash kept


def test_parse_amc_transaction_statement_skips_non_trades(tmp_path):
    p = tmp_path / "tx2.csv"
    p.write_text(
        "PRODUCT_CODE,SCHEME_NAME,TRADE_DATE,TRANSACTION_TYPE,AMOUNT,UNITS,PRICE\n"
        "B292G,Focused,28-SEP-2006,Registered,0,,\n"
        "B292G,Focused,01-JAN-2007,Purchase,5000,100,50\n",
        encoding="utf-8",
    )
    rows, warns = parse_mf_statement(str(p))
    assert warns == []
    assert len(rows) == 1
    assert rows[0]["side"] == "PURCHASE"
    assert rows[0]["trade_date"] == "2007-01-01"


def test_parse_current_valuation(tmp_path):
    p = tmp_path / "cv.csv"
    p.write_text(
        "AMC Name,SCHEMECODE,Scheme,Type,Folio,Investor Name,PAN,Unit Balance,"
        "NAV Date,Current Value(Rs.),Cost Value(Rs.)\n"
        "HDFC,AFWGT,HDFC Arbitrage DP G,Equity,999,X,ABC,55068.497,2026-11-13,1210735.98,1166684.17\n"
        "HDFC,8017,Aggressive Hybrid,Equity,999,X,ABC,0,2026-11-13,0,0\n",
        encoding="utf-8",
    )
    recs = parse_current_valuation(str(p))
    assert len(recs) == 1  # zero-unit row skipped
    r = recs[0]
    assert r["scheme_code"] == "AFWGT"
    assert abs(r["units"] - 55068.497) < 1e-3
    assert r["nav_date"] == "2026-11-13"
    assert abs(r["nav"] - 1210735.98 / 55068.497) < 1e-3
    assert abs(r["current_value"] - 1210735.98) < 1e-2
