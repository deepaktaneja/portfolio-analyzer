"""Tests for MF data layer (cache, loaders), importers and service (Stage 7)."""
from __future__ import annotations

import csv
import datetime as dt

from models.mf_analytics import MFNavCache, get_mf_analytics_session


def _temp_session(tmp_path):
    return get_mf_analytics_session(db_url=f"sqlite:///{tmp_path / 'mf_analytics.sqlite3'}")


# ---------------------------------------------------------------------------
# Storage round-trips
# ---------------------------------------------------------------------------

def test_save_and_load_scheme_holdings(tmp_path):
    from mf.data import load_scheme_holdings_from_store, save_scheme_holdings
    s = _temp_session(tmp_path)
    try:
        recs = [
            {"scheme_code": "A", "as_of_date": "2026-10-31", "stock_isin": "INE1", "stock_name": "X", "weight_pct": 10, "sector": "IT", "market_cap": "Large"},
            {"scheme_code": "A", "as_of_date": "2026-10-31", "stock_isin": "INE2", "stock_name": "Y", "weight_pct": 5, "sector": "Banks", "market_cap": "Large"},
        ]
        n = save_scheme_holdings(recs, session=s)
        assert n == 2
        holdings, as_of = load_scheme_holdings_from_store(session=s)
        assert len(holdings["A"]) == 2
        assert as_of["A"] == "2026-10-31"
    finally:
        s.close()


def test_save_and_get_fund_metadata(tmp_path):
    from mf.data import get_fund_metadata_map, save_fund_metadata
    s = _temp_session(tmp_path)
    try:
        save_fund_metadata([{"scheme_code": "A", "scheme_name": "Fund A", "expense_ratio_direct": 0.5, "category": "Large Cap"}], session=s)
        meta = get_fund_metadata_map(session=s)
        assert meta["A"]["scheme_name"] == "Fund A"
        assert meta["A"]["expense_ratio_direct"] == 0.5
    finally:
        s.close()


def test_nav_cache_and_freshness(tmp_path):
    from mf.data import MFDataService
    s = _temp_session(tmp_path)
    try:
        # Insert NAV rows directly into the cache
        today = dt.date.today().isoformat()
        s.add(MFNavCache(scheme_code="A", as_of_date=today, nav=10.5, source="amfi", provider="mfapi", fetched_at=today))
        s.commit()
        svc = MFDataService(session=s)
        recs = svc.cached_nav_history("A")
        assert recs and recs[-1][1] == 10.5
        fresh = svc.nav_freshness("A")
        assert fresh["status"] == "ok"
    finally:
        s.close()


# ---------------------------------------------------------------------------
# CSV loaders
# ---------------------------------------------------------------------------

def test_load_holdings_csv(tmp_path):
    from mf.data import load_scheme_holdings_csv
    p = tmp_path / "holdings.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scheme_code", "as_of_date", "stock_isin", "stock_name", "weight_pct", "sector", "market_cap"])
        w.writerow(["A", "2026-10-31", "INE1", "X", "10", "IT", "Large"])
    recs = load_scheme_holdings_csv(str(p))
    assert len(recs) == 1
    assert recs[0]["stock_isin"] == "INE1"


def test_load_metadata_csv(tmp_path):
    from mf.data import load_fund_metadata_csv
    p = tmp_path / "metadata.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scheme_code", "scheme_name", "category", "expense_ratio_direct", "aum_crore"])
        w.writerow(["A", "Fund A", "Large Cap", "0.55", "5000"])
    recs = load_fund_metadata_csv(str(p))
    assert recs[0]["expense_ratio_direct"] == 0.55
    assert recs[0]["aum_crore"] == 5000.0


# ---------------------------------------------------------------------------
# MF statement parser
# ---------------------------------------------------------------------------

def test_parse_mf_statement(tmp_path):
    from mf.importers import detect_mf_statement, parse_mf_statement
    p = tmp_path / "mf_statement.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "scheme_code", "scheme_name", "transaction_type", "units", "nav", "amount"])
        w.writerow(["2024-01-01", "A", "Fund A", "purchase", "100", "15.5", "1550"])
        w.writerow(["2024-06-01", "A", "Fund A", "redemption", "20", "17.0", "340"])
    assert detect_mf_statement(str(p)) is True
    rows, _warnings = parse_mf_statement(str(p))
    assert len(rows) == 2
    assert rows[0]["side"] == "PURCHASE"
    assert rows[1]["side"] == "REDEMPTION"


# ---------------------------------------------------------------------------
# Service integration
# ---------------------------------------------------------------------------

def test_build_mf_context_end_to_end():
    from mf.service import build_mf_context
    base = dt.date(2021, 1, 1)
    nav = [((base + dt.timedelta(days=30 * i)).isoformat(), round(100 * 1.10 ** (i / 12), 4)) for i in range(37)]
    txs = [{"scheme_code": "A", "scheme_name": "Fund A", "trade_date": "2021-01-01", "side": "PURCHASE", "units": 100, "amount": 10000}]
    holdings = {"A": [{"stock_isin": "INE1", "stock_name": "X", "weight_pct": 10, "sector": "IT", "market_cap": "Large"}]}
    ctx = build_mf_context(txs, {"A": nav}, holdings_by_scheme=holdings,
                           as_of_by_scheme={"A": "2026-11-01"},
                           fund_metadata={"A": {"scheme_name": "Fund A", "expense_ratio_direct": 0.5}},
                           end_date="2026-09-30")
    assert ctx["total_value"] > 0
    assert ctx["scheme_rows"][0]["xirr"] is not None
    assert ctx["quality"][0]["rating"] in ("Strong", "Adequate", "Watch", "Review")
    assert len(ctx["disclosures"]) == 1
