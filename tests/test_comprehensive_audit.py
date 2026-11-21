# Comprehensive audit: bottom-up conviction SIP sizing (Rs 3L is a ceiling, not a target).
from __future__ import annotations

from performance import stage4_signals as s4


def _rows(*specs):
    return [
        {"security_id": sid, "sip_multiplier": mult, "cmp": 100.0, "incubation": False}
        for sid, mult in specs
    ]

def test_bottom_up_never_inflates_to_ceiling():
    # 2 x 2.5x winners (25k each) + 3 x 1.0x cruising (10k each) = 80,000, NOT 3,00,000.
    rows = _rows(("W1", 2.5), ("W2", 2.5), ("C1", 1.0), ("C2", 1.0), ("C3", 1.0))
    alloc = s4.compute_bottom_up_sip_allocation(
        rows, base_unit_sip=10000.0, max_budget_ceiling=300000.0, max_single_stock_sip=35000.0)
    assert alloc["natural_total_sip"] == 80000.0
    assert alloc["total_allocated"] == 80000.0
    assert alloc["parked_cash"] == 220000.0
    assert alloc["scaled_down"] is False
    by_id = {r["security_id"]: r["recommended_sip_rupees"] for r in alloc["rows"]}
    assert by_id == {"W1": 25000.0, "W2": 25000.0, "C1": 10000.0, "C2": 10000.0, "C3": 10000.0}


def test_natural_sip_per_lane():
    assert s4.compute_stock_natural_sip(2.5, 10000) == 25000.0
    assert s4.compute_stock_natural_sip(1.0, 10000) == 10000.0
    assert s4.compute_stock_natural_sip(0.5, 10000) == 5000.0
    assert s4.compute_stock_natural_sip(0.0, 10000) == 0.0

def test_ceiling_only_scales_down_when_exceeded():
    rows = _rows(*[(f"W{i}", 2.5) for i in range(10)])  # 250,000 natural
    alloc = s4.compute_bottom_up_sip_allocation(
        rows, base_unit_sip=10000.0, max_budget_ceiling=100000.0, max_single_stock_sip=35000.0)
    assert alloc["natural_total_sip"] == 250000.0
    assert alloc["scaled_down"] is True
    assert alloc["total_allocated"] <= 100000.0

def test_overallocation_cap_125():
    # RELIND at 1.26x AER must pause SIP (0.0x); 1.24x still qualifies for 2.5x.
    st = s4.StockInput(security_id="RELIND", symbol="RELIND", quantity=100, avg_buy_price=100.0,
                       invested_cost=10000.0, market_value=12600.0, xirr=0.50,
                       price_series=[100.0] * 260)
    base = {"cmp": 100.0, "dma_50": 95.0, "dma_200": 90.0, "annual_volatility": 0.20,
            "dma200_distance_pct": 0.111}
    m126 = dict(base); m126["aer"] = 1.26
    m124 = dict(base); m124["aer"] = 1.24
    assert s4.compute_sip_multiplier(st, m126, 0.12) == 0.0
    assert s4.compute_sip_multiplier(st, m124, 0.12) == 2.5

def test_watchlist_entries_get_natural_sip():
    rows = _rows(("C1", 1.0))
    wl = [{"ticker": "NEW1", "dma200_distance_pct": 0.10},
          {"ticker": "NEW2", "dma200_distance_pct": -0.05}]
    alloc = s4.compute_bottom_up_sip_allocation(
        rows, watchlist_entries=wl, base_unit_sip=10000.0, max_budget_ceiling=300000.0)
    # C1=10k, NEW1 healthy=10k, NEW2 below-200DMA=5k => 25,000
    assert alloc["total_allocated"] == 25000.0
    by_ticker = {r["ticker"]: r["recommended_sip_rupees"] for r in alloc["watchlist_rows"]}
    assert by_ticker == {"NEW1": 10000.0, "NEW2": 5000.0}


def test_watchlist_persistence_company_name_and_unified_price_history(tmp_path):
    """Watchlist persistence + ISIN/ticker unified price linking + company name.

    Mirrors the REST contract: POST (add_watchlist_stock) -> GET
    (get_watchlist_stocks) -> DELETE (delete_watchlist_stock), plus the price
    fetcher's ISIN/ticker normalisation so both identifiers share one series.
    """
    from market_data.prices import get_stock_prices_unified, normalize_security_id
    from market_data.repository import MarketDataStore

    db = str(tmp_path / "market_data.sqlite3")
    store = MarketDataStore(db)
    isin = "INE192B01031"
    ticker = "SJS.NS"

    # --- POST /api/watchlist ---
    row_id = store.add_watchlist_stock(
        isin=isin, nse_ticker=ticker, company_name="SJS Enterprises Ltd", manual_cmp=1234.5)
    assert row_id > 0

    # --- GET /api/watchlist (persists across a fresh store handle) ---
    wl = MarketDataStore(db).get_watchlist_stocks()
    assert len(wl) == 1
    assert wl[0]["isin"] == isin
    assert wl[0]["nse_ticker"] == ticker
    assert wl[0]["company_name"] == "SJS Enterprises Ltd"

    # Re-adding the same stock is idempotent (no duplicate row / key).
    store.add_watchlist_stock(
        isin=isin, nse_ticker=ticker, company_name="SJS Enterprises Ltd", manual_cmp=1300.0)
    assert len(store.get_watchlist_stocks()) == 1

    # --- Seed one price series under the canonical NSE ticker ---
    store.upsert_stock_prices([
        {"security_id": ticker, "trade_date": "2024-01-01", "close_price": 100.0, "adjusted_close": 100.0},
        {"security_id": ticker, "trade_date": "2024-01-02", "close_price": 101.5, "adjusted_close": 101.5},
        {"security_id": ticker, "trade_date": "2024-01-03", "close_price": 99.0, "adjusted_close": 99.0},
    ])

    # --- Unified price history by ISIN, bare ticker and suffixed ticker ---
    by_isin = get_stock_prices_unified(isin, None, "2024-01-31", db_path=db)
    by_bare = get_stock_prices_unified("SJS", None, "2024-01-31", db_path=db)
    by_ticker = get_stock_prices_unified(ticker, None, "2024-01-31", db_path=db)
    assert by_isin and by_isin == by_bare == by_ticker
    assert [c for _, c in by_isin] == [100.0, 101.5, 99.0]

    # Normalisation links both identifiers to the same canonical ticker.
    ident_isin = normalize_security_id(isin, db_path=db)
    ident_bare = normalize_security_id("SJS", db_path=db)
    assert ident_isin["nse_ticker"] == ticker
    assert ident_bare["nse_ticker"] == ticker
    assert isin in ident_isin["aliases"] and ticker in ident_isin["aliases"]

    # --- DELETE /api/watchlist/{id} ---
    assert store.delete_watchlist_stock(stock_id=row_id) == 1
    assert store.get_watchlist_stocks() == []


def test_backtest_universe_bulk_cache_and_gating(tmp_path, monkeypatch):
    """Stage 4E-1: bulk fetch+cache, universe assembly, delisted skip, IPO gating.

    Verifies active + watchlist + closed (net_qty==0) + benchmark ETFs are loaded,
    MUFNDF is excluded, delisted tickers are skipped and remembered (so repeat
    runs make zero API calls), and IPO gating honours the 200-day rule.
    """
    import pandas as pd

    from market_data.repository import MarketDataStore
    from performance import backtest_data as bd

    db = str(tmp_path / "market_data.sqlite3")
    store = MarketDataStore(db)

    # Persisted watchlist stocks (SJS = IPO, YESBANK = also a closed position)
    store.add_watchlist_stock(isin="INE192B01031", nse_ticker="SJS.NS",
                              company_name="SJS Enterprises Ltd")
    store.add_watchlist_stock(isin="INE528G01035", nse_ticker="YESBANK.NS",
                              company_name="Yes Bank")

    # One ticker already cached -> must NOT be re-fetched.
    store.upsert_stock_prices([
        {"security_id": "RELIANCE.NS", "trade_date": "2024-01-01", "close_price": 100.0},
        {"security_id": "RELIANCE.NS", "trade_date": "2024-01-02", "close_price": 101.0},
    ])

    holdings = pd.DataFrame([
        {"isin": "INE002A01018", "symbol": "RELIANCE", "quantity": 50.0},
        {"isin": "INF000000001", "symbol": "MUFNDF", "quantity": 10.0},  # excluded
    ])
    txs = [
        {"isin": "INE528G01035", "buy_sell": "BUY", "quantity": 100},   # YESBANK closed
        {"isin": "INE528G01035", "buy_sell": "SELL", "quantity": 100},
        {"isin": "DHFL.NS", "buy_sell": "BUY", "quantity": 10},         # delisted
        {"isin": "DHFL.NS", "buy_sell": "SELL", "quantity": 10},
    ]

    downloaded = {"tickers": []}

    def fake_download(tickers, period="11y"):
        downloaded["tickers"] = list(tickers)
        idx = pd.date_range("2020-01-01", periods=260, freq="B")
        cols = {}
        for t in tickers:
            if t == "DHFL.NS":
                continue  # delisted -> empty data (no columns)
            if t == "SJS.NS":
                vals = [float("nan")] * 255 + [float(10 + i) for i in range(5)]  # IPO: last 5 days
            else:
                vals = [float(10 + i) for i in range(260)]
            cols[(t, "Close")] = list(vals)
            cols[(t, "Adj Close")] = list(vals)
        return pd.DataFrame(cols, index=idx)  # tuple keys -> MultiIndex columns

    monkeypatch.setattr(bd, "_bulk_download", fake_download)

    res = bd.load_backtest_universe(market_db_path=db, txs=txs, holdings=holdings)

    # Universe: active + watchlist + closed + ETFs; MUFNDF excluded.
    assert "MUFNDF.NS" not in res.tickers
    assert {"RELIANCE.NS", "YESBANK.NS", "SJS.NS", "^NSEI",
            "^CRSLDX"} <= set(res.tickers)
    # Cached ticker not re-fetched; all uncached fetched in ONE download call.
    assert "RELIANCE.NS" in res.cached_tickers
    assert "RELIANCE.NS" not in downloaded["tickers"]

    # Delisted ticker skipped cleanly.
    assert "DHFL.NS" in res.skipped_tickers
    assert "DHFL.NS" not in res.newly_fetched

    # IPO gating: only 5 valid days for SJS.NS (< 200) -> not ready.
    assert res.first_valid_date.get("SJS.NS") is not None
    assert not bd.is_ipo_ready("SJS.NS", market_db_path=db)

    # Cached reads are local and correct.
    assert len(bd.cached_price_series("SJS.NS", market_db_path=db)) == 5

    # Second run -> everything cached / delisted remembered -> ZERO API calls.
    calls = {"n": 0}

    def counting_download(tickers, period="11y"):
        calls["n"] += 1
        return fake_download(tickers, period)

    monkeypatch.setattr(bd, "_bulk_download", counting_download)
    res2 = bd.load_backtest_universe(market_db_path=db, txs=txs, holdings=holdings)
    assert calls["n"] == 0
    assert res2.newly_fetched == []
    assert "DHFL.NS" in res2.skipped_tickers

    # Verification summary: cached date range + trading-day count per ticker.
    summary = bd.universe_price_summary(market_db_path=db)
    assert summary["SJS.NS"]["count"] == 5
    assert summary["RELIANCE.NS"]["count"] == 2


def _stage4_engine_res(tmp_path, monkeypatch, years=3):
    """Shared Stage 4E-2 fixture: build an isolated market DB with fake prices and
    return a full ``run_backtest`` result over a steady-uptrend window."""
    import pandas as pd

    from market_data.repository import MarketDataStore
    from performance import backtest_data as bd
    from performance.stage4_backtest import BacktestConfig, run_backtest

    db = str(tmp_path / "market_data.sqlite3")
    store = MarketDataStore(db)

    # Watchlist (SJS is a late IPO; YESBANK links the closed-position ISIN).
    store.add_watchlist_stock(isin="INE192B01031", nse_ticker="SJS.NS", company_name="SJS")
    store.add_watchlist_stock(isin="INE018A01019", nse_ticker="RADICO.NS", company_name="Radico")
    store.add_watchlist_stock(isin="INE528G01035", nse_ticker="YESBANK.NS", company_name="Yes Bank")

    holdings = pd.DataFrame([
        {"isin": "INE002A01018", "symbol": "RELIANCE", "quantity": 10.0},
    ])
    # YESBANK is a closed position (net qty 0) with a real first-buy trade date.
    txs = [
        {"isin": "INE528G01035", "buy_sell": "BUY", "quantity": 100,
         "trade_date": "2019-01-01", "gross_amount": 15000.0},
        {"isin": "INE528G01035", "buy_sell": "SELL", "quantity": 100,
         "trade_date": "2021-06-01", "gross_amount": 18000.0},
    ]
    # RELIANCE: real monthly SIP buys (Rs 50,000) so fresh capital scales over time
    # and the engine has a genuine monthly net buy inflow to deploy (Req 4a).
    for dt in pd.date_range("2018-01-15", "2021-01-15", freq="MS"):
        txs.append({"isin": "INE002A01018", "buy_sell": "BUY", "quantity": 10,
                    "trade_date": dt.strftime("%Y-%m-%d"), "gross_amount": 50000.0})

    dates = pd.date_range("2018-01-01", periods=800, freq="B")

    def fake_download(tickers, period="8y"):
        cols = {}
        bench = {"^NSEI": 20000.0, "^CRSLDX": 5000.0}
        for t in tickers:
            if t == "DHFL.NS":
                continue  # delisted -> empty
            base = bench.get(t, 100.0)
            vals = [base + i * 0.05 for i in range(len(dates))]  # steady uptrend
            if t == "SJS.NS":
                vals = [float("nan")] * (len(dates) - 5) + vals[-5:]  # IPO: 5 valid days
            cols[(t, "Close")] = list(vals)
            cols[(t, "Adj Close")] = list(vals)
        return pd.DataFrame(cols, index=dates)

    monkeypatch.setattr(bd, "_bulk_download", fake_download)

    cfg = BacktestConfig(years=years, include_watchlist=True, include_closed_positions=True,
                         base_unit_sip=10000.0, max_monthly_ceiling=200000.0,
                         market_db_path=db)
    return run_backtest(cfg, txs=txs, holdings=holdings)


def test_backtest_engine_zero_nan_and_cashflow_matching(tmp_path, monkeypatch):
    """Stage 4E-2: walk-forward engine returns finite values and every benchmark
    deploys the EXACT same rupees on the EXACT same dates as the strategy."""
    import math

    res = _stage4_engine_res(tmp_path, monkeypatch)

    # 1. Zero NaN / Infinity anywhere in the result tree.
    def _walk(obj, path="root"):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _walk(v, f"{path}[{i}]")
        elif isinstance(obj, float):
            assert math.isfinite(obj), f"non-finite value at {path}: {obj}"

    _walk(res)

    # 2. Exact cash-flow matching: all benchmarks deploy the same rupees/dates.
    cfs = res["cashflows"]
    assert cfs, "strategy should deploy cash in a 3-year uptrend"
    invested = res["scorecard"]["invested"]
    assert invested > 0
    assert set(res["benchmarks"]) == {"equal_weight", "nifty50", "nifty500"}
    for name, bench in res["benchmarks"].items():
        assert bench["cashflows"] == cfs, f"{name} cash-flow dates/amounts differ"
        assert abs(sum(a for _d, a in bench["cashflows"]) - invested) < 1e-6

    # 3. Benchmarks resolved from the local cache (Nifty 50 / Nifty 500).
    assert res["universe"]["benchmarks"]["nifty50"] == "^NSEI"
    assert res["universe"]["benchmarks"]["nifty500"] == "^CRSLDX"

    # 4. Closed position participates in the simulation + attribution.
    assert "YESBANK.NS" in {r["ticker"] for r in res["attribution"]}

    # 5. Scorecard completeness and non-degeneracy.
    sc = res["scorecard"]
    for key in ("invested", "final_net_value", "post_tax_xirr_pct", "pre_tax_xirr_pct",
                "max_drawdown_pct", "return_drawdown_ratio", "taxes_paid",
                "friction_paid", "trade_count"):
        assert key in sc
    assert sc["trade_count"] > 0
    assert sc["final_net_value"] > 0
    assert res["daily_curve"] and res["monthly_curve"]
    assert res["calendar_year_returns"]


def test_scorecard_and_calendar_returns_have_no_none_or_nan(tmp_path, monkeypatch):
    """Every Comparison-Scorecard cell (Strategy + Equal-Weight / Nifty 50 /
    Nifty 500) and every Calendar-Year-Returns cell is a real finite number —
    never None / NaN."""
    import math

    res = _stage4_engine_res(tmp_path, monkeypatch)

    def _check(value, path):
        assert value is not None, f"None cell at {path}"
        assert isinstance(value, (int, float)) and not isinstance(value, bool), \
            f"non-numeric cell at {path}: {value!r}"
        assert math.isfinite(float(value)), f"NaN/Inf cell at {path}: {value!r}"

    # 1. Strategy scorecard: no missing cells.
    for key, value in res["scorecard"].items():
        _check(value, f"scorecard.{key}")

    # 2. Strategy Calendar-Year Returns: no missing cells.
    assert res["calendar_year_returns"], "calendar_year_returns should not be empty"
    for year, value in res["calendar_year_returns"].items():
        _check(value, f"calendar_year_returns[{year}]")

    # 3. Every benchmark (Equal-Weight, Nifty 50, Nifty 500) has full metrics.
    assert set(res["benchmarks"]) == {"equal_weight", "nifty50", "nifty500"}
    for name, bench in res["benchmarks"].items():
        for key in ("invested", "final_value", "xirr", "post_tax_xirr_pct",
                    "pre_tax_xirr_pct", "max_drawdown_pct", "return_drawdown_ratio"):
            assert key in bench, f"{name} benchmark missing '{key}'"
            _check(bench[key], f"benchmarks.{name}.{key}")
        assert bench["final_value"] > 0, f"{name} benchmark has no terminal value"
        assert bench["calendar_year_returns"], f"{name} has no calendar-year returns"
        for year, value in bench["calendar_year_returns"].items():
            _check(value, f"benchmarks.{name}.calendar_year_returns[{year}]")

    # 4. Nifty 50 / Nifty 500 resolved from the local cache.
    assert res["universe"]["benchmarks"]["nifty50"] == "^NSEI"
    assert res["universe"]["benchmarks"]["nifty500"] == "^CRSLDX"


# --------------------------------------------------------------------------- #
# Stage 4E realism overhaul: cash-flow floor, TRI yield, permanent lockout and
# the 3-year watchlist activation cap.
# --------------------------------------------------------------------------- #
class _StubPrices:
    """Minimal PriceIndex stand-in for pure benchmark-curve unit tests."""

    def __init__(self, series):
        self._s = series

    def has(self, ticker):
        return bool(self._s.get(ticker))

    def price_on(self, ticker, d):
        best = None
        for dd, p in self._s.get(ticker, []):
            if dd <= d:
                best = p
            else:
                break
        return best

    def series_upto(self, ticker, d):
        return [p for dd, p in self._s.get(ticker, []) if dd <= d]

    def dates_upto(self, ticker, d):
        return [dd for dd, _p in self._s.get(ticker, []) if dd <= d]


def test_monthly_net_buy_inflow_floored_at_zero():
    """Fresh capital each month = max(0, net real buy inflow); net-sell months
    inject nothing (never a negative cashflow or artificial benchmark sell)."""
    from performance.stage4_backtest import _monthly_net_buy_inflow

    txs = [
        {"buy_sell": "BUY", "trade_date": "2020-01-10", "gross_amount": 60000.0},
        {"buy_sell": "SELL", "trade_date": "2020-01-20", "gross_amount": 20000.0},
        {"buy_sell": "BUY", "trade_date": "2020-02-10", "gross_amount": 10000.0},
        {"buy_sell": "SELL", "trade_date": "2020-02-20", "gross_amount": 80000.0},
    ]
    net = _monthly_net_buy_inflow(txs)
    assert net[(2020, 1)] == 40000.0
    assert net[(2020, 2)] == -70000.0
    # Downstream the contribution is floored at zero.
    assert max(0.0, net[(2020, 1)]) == 40000.0
    assert max(0.0, net[(2020, 2)]) == 0.0


def test_tri_index_benchmark_accrues_dividend_yield():
    """^NSEI / ^CRSLDX TRI = price return + compounded 1.30% dividend yield, so the
    TRI terminal value strictly exceeds the price-only terminal value."""
    from datetime import date

    from performance.stage4_backtest import _benchmark_daily_curve

    dates = [date(2020, 1, 1), date(2020, 6, 1), date(2021, 1, 1)]
    prices = _StubPrices({"^NSEI": [
        (date(2020, 1, 1), 100.0),
        (date(2020, 6, 1), 110.0),
        (date(2021, 1, 1), 120.0),
    ]})
    flows = [(date(2020, 1, 1), -100000.0)]
    price_only, _ = _benchmark_daily_curve(prices, ["^NSEI"], flows, dates, dividend_yield=0.0)
    tri, _ = _benchmark_daily_curve(prices, ["^NSEI"], flows, dates, dividend_yield=0.013)
    # Day 0: no accrual yet (identical).
    assert abs(tri[0][1] - price_only[0][1]) < 1e-6
    # After ~1 year the TRI curve is strictly above price-only by ~1.3%.
    assert tri[-1][1] > price_only[-1][1]
    assert 0.005 < (tri[-1][1] / price_only[-1][1] - 1.0) < 0.02


def test_permanent_lockout_blocks_reentry(monkeypatch):
    """A stock flagged permanent_exited (HARD_STOP_LOSS) is never re-bought, even
    when the allocator would otherwise deploy a fresh SIP into it."""
    from datetime import date, timedelta

    from performance import stage4_backtest as s4b

    start = date(2020, 1, 1)
    series = [(start + timedelta(days=i), 100.0 + i * 0.1) for i in range(260)]
    prices = _StubPrices({"LOCKED.NS": series})
    d = series[-1][0]

    def _fake_alloc(rows, base_unit_sip=0.0, max_budget_ceiling=0.0, **kw):
        return {"total_allocated": 10000.0,
                "rows": [{"security_id": "LOCKED.NS", "recommended_sip_rupees": 10000.0}],
                "natural_total_sip": 10000.0, "parked_cash": 0.0, "scaled_down": False}

    monkeypatch.setattr(s4b, "compute_bottom_up_sip_allocation", _fake_alloc)
    cfg = s4b.BacktestConfig(base_unit_sip=10000.0, max_monthly_ceiling=200000.0)

    def _run(permanent):
        st = s4b._StockState(ticker="LOCKED.NS", isin=None,
                             first_buy_date=start, sources={"active"})
        st.activation_date = start
        st.permanent_exited = permanent
        s4b._monthly_step([st], prices, d, 10000.0, cfg, {"ltcg_by_fy": {}},
                          None, [], {"friction": 0.0, "taxes": 0.0},
                          monthly_inflow=10000.0)
        return st.trades, st.qty

    # Control: without the lockout the stock IS bought (the guard is what blocks it).
    trades_ok, qty_ok = _run(False)
    assert trades_ok >= 1 and qty_ok > 0
    # Locked out: HARD_STOP_LOSS -> never re-entered.
    trades_locked, qty_locked = _run(True)
    assert trades_locked == 0 and qty_locked == 0.0


def test_watchlist_activation_capped_to_recent_three_years(tmp_path, monkeypatch):
    """Prospective watchlist additions never activate before end_date - 3 years,
    regardless of the (7-year) backtest window."""
    from datetime import date, timedelta

    import pandas as pd

    from market_data.repository import MarketDataStore
    from performance import backtest_data as bd
    from performance.stage4_backtest import BacktestConfig, run_backtest

    db = str(tmp_path / "market_data.sqlite3")
    store = MarketDataStore(db)
    store.add_watchlist_stock(isin="INE999999999", nse_ticker="WATCH.NS",
                              company_name="Watch Co")

    dates = pd.date_range("2018-01-01", periods=2100, freq="B")  # ~8 years

    def fake_download(tickers, period="8y"):
        cols = {}
        bench = {"^NSEI": 20000.0, "^CRSLDX": 5000.0}
        for t in tickers:
            base = bench.get(t, 100.0)
            vals = [base + i * 0.05 for i in range(len(dates))]
            cols[(t, "Close")] = list(vals)
            cols[(t, "Adj Close")] = list(vals)
        return pd.DataFrame(cols, index=dates)

    monkeypatch.setattr(bd, "_bulk_download", fake_download)
    cfg = BacktestConfig(years=7, include_watchlist=True, include_closed_positions=False,
                         base_unit_sip=10000.0, max_monthly_ceiling=200000.0,
                         market_db_path=db)
    res = run_backtest(cfg, txs=[], holdings=None)

    data_end = date.fromisoformat(res["period"]["end"])
    cutoff = data_end.replace(year=data_end.year - 3)  # end_date - 3 years
    act = res["universe"]["activations"].get("WATCH.NS")
    assert act is not None, "watchlist stock should activate within the window"
    assert date.fromisoformat(act) >= cutoff
    assert data_end - timedelta(days=366 * 3) <= date.fromisoformat(act)


# --------------------------------------------------------------------------- #
# .NS / ISIN alias resolution, full-pool deployment and realized-sell tracking.
# --------------------------------------------------------------------------- #
def test_price_index_resolves_isin_cached_series(tmp_path):
    """Prices cached under an ISIN must still resolve for the .NS ticker key, so
    active/closed/watchlist stocks are never silently missed (Bug 1)."""
    from datetime import date

    from market_data.repository import MarketDataStore
    from performance.stage4_backtest import PriceIndex

    db = str(tmp_path / "market_data.sqlite3")
    store = MarketDataStore(db)
    isin = "INE002A01018"  # RELIANCE
    # Cache ONLY under the ISIN — no .NS rows exist at all.
    store.upsert_stock_prices([
        {"security_id": isin, "trade_date": "2020-01-01", "close_price": 100.0},
        {"security_id": isin, "trade_date": "2020-01-02", "close_price": 101.0},
    ])
    pi = PriceIndex({"RELIANCE.NS"}, db, isin_by_ticker={"RELIANCE.NS": isin})
    assert pi.has("RELIANCE.NS")
    assert pi.price_on("RELIANCE.NS", date(2020, 1, 2)) == 101.0
    assert len(pi.series("RELIANCE.NS")) == 2


def test_execute_sell_tracks_gross_proceeds():
    """Realized-sell tracking records GROSS proceeds (before friction & tax) so
    the Stage 4 Engine scorecard can report real sells instead of Rs 0 (Bug 3)."""
    from datetime import date

    from performance.stage4_backtest import _execute_sell, _Lot, _StockState

    st = _StockState(ticker="X.NS", isin=None, first_buy_date=date(2020, 1, 1),
                     sources={"active"})
    st.lots = [_Lot(qty=100, price=100.0, date=date(2020, 1, 1))]
    st.invested = 10000.0
    net, _friction, _tax = _execute_sell(st, date(2021, 6, 1), 150.0, 1.0, {"ltcg_by_fy": {}})
    assert st.gross_proceeds == 100 * 150.0      # gross, before friction & tax
    assert st.proceeds < st.gross_proceeds       # net is lower
    assert net == st.proceeds


def test_record_sell_stats_bucket_accumulation():
    """Each sell reason bucket tracks count, gross, net, tax and friction."""
    from performance.stage4_backtest import _record_sell_stats

    stats = {"friction": 0.0, "taxes": 0.0, "sells": {}}
    _record_sell_stats(stats, "HARD_STOP_LOSS", 15000.0, 37.5, 0.0)
    _record_sell_stats(stats, "HARD_STOP_LOSS", 5000.0, 12.5, 0.0)
    _record_sell_stats(stats, "LAGGARD_EXIT", 20000.0, 50.0, 0.0)
    stop = stats["sells"]["HARD_STOP_LOSS"]
    assert stop["count"] == 2
    assert stop["gross"] == 20000.0
    assert stop["net"] == 19950.0
    assert stop["tax"] == 0.0 and stop["friction"] == 50.0
    lag = stats["sells"]["LAGGARD_EXIT"]
    assert lag["count"] == 1 and lag["gross"] == 20000.0


def test_build_sell_breakdown_rows_and_totals():
    """Breakdown rows cover every trigger in SELL_REASON_ORDER with exact totals."""
    from performance.stage4_backtest import SELL_REASON_ORDER, _build_sell_breakdown

    stats = {"friction": 0.0, "taxes": 0.0, "sells": {
        "CONCENTRATION_TRIM": {"count": 1, "gross": 1000.0, "net": 980.0,
                               "tax": 0.0, "friction": 20.0},
        "CATASTROPHE_GTT": {"count": 1, "gross": 900.0, "net": 882.0,
                            "tax": 0.0, "friction": 18.0},
        "LAGGARD_EXIT": {"count": 2, "gross": 5000.0, "net": 4900.0,
                         "tax": 0.0, "friction": 100.0},
    }}
    out = _build_sell_breakdown(stats)
    assert [r["reason"] for r in out["rows"]] == list(SELL_REASON_ORDER)
    by_reason = {r["reason"]: r for r in out["rows"]}
    assert by_reason["LAGGARD_EXIT"]["count"] == 2
    assert by_reason["LAGGARD_EXIT"]["gross"] == 5000.0
    assert by_reason["CONCENTRATION_TRIM"]["gross"] == 1000.0
    assert by_reason["CATASTROPHE_GTT"]["gross"] == 900.0
    # Triggers that never fired are still present, with explicit zero rows.
    assert by_reason["HARD_STOP_LOSS"]["count"] == 0
    assert by_reason["HARD_STOP_LOSS"]["gross"] == 0.0
    assert by_reason["GTT_STOP_LOSS"]["count"] == 0
    assert out["total_trades"] == 4
    assert out["total_gross_proceeds"] == 6900.0
    assert out["total_net_proceeds"] == 6762.0


def test_build_sell_breakdown_empty_stats():
    """Empty stats still yields all five zero rows with zeroed totals."""
    from performance.stage4_backtest import SELL_REASON_ORDER, _build_sell_breakdown

    out = _build_sell_breakdown({"friction": 0.0, "taxes": 0.0, "sells": {}})
    assert [r["reason"] for r in out["rows"]] == list(SELL_REASON_ORDER)
    assert all(r["count"] == 0 and r["gross"] == 0.0 for r in out["rows"])
    assert out["total_trades"] == 0
    assert out["total_gross_proceeds"] == 0.0
    assert out["total_net_proceeds"] == 0.0


def test_monthly_step_deploys_full_budget(monkeypatch):
    """With healthy stocks the engine scales the Base Unit SIP so ~100% of the
    month's pool is deployed (no 95% idle cash); with all stocks paused (0.0x) the
    pool stays fully in the Liquid Reserve (Bug 2)."""
    from datetime import date, timedelta

    from performance import stage4_backtest as s4b

    start = date(2020, 1, 1)
    series = [(start + timedelta(days=i), 100.0) for i in range(260)]
    names = ("A.NS", "B.NS", "C.NS", "D.NS")
    prices = _StubPrices({t: series for t in names})
    d = series[-1][0]
    cfg = s4b.BacktestConfig(base_unit_sip=10000.0, max_monthly_ceiling=200000.0)

    def _make_states():
        sts = []
        for t in names:
            st = s4b._StockState(ticker=t, isin=None, first_buy_date=start,
                                 sources={"active"})
            st.activation_date = start
            sts.append(st)
        return sts

    def _eval_at(mult):
        def _fake(stocks, totals, bx, as_of_date=None, **kwargs):
            return [{"security_id": si.security_id, "sip_multiplier": mult,
                     "cmp": 100.0, "incubation": False, "dma_50": 90.0,
                     "dma_200": 80.0} for si in stocks]
        return _fake

    # Healthy (Lane 2, 1.0x) across 4 stocks: ~full Rs 2L deploys, ~0 idle.
    monkeypatch.setattr(s4b, "evaluate_signals", _eval_at(1.0))
    states = _make_states()
    cash = s4b._monthly_step(states, prices, d, 0.0, cfg, {"ltcg_by_fy": {}},
                             None, [], {"friction": 0.0, "taxes": 0.0},
                             monthly_inflow=200000.0)
    deployed = sum(st.invested for st in states)
    assert deployed >= 0.88 * 200000.0
    assert cash <= 0.12 * 200000.0

    # All paused (0.0x): nothing deploys, the full pool stays parked in cash.
    monkeypatch.setattr(s4b, "evaluate_signals", _eval_at(0.0))
    states2 = _make_states()
    cash2 = s4b._monthly_step(states2, prices, d, 0.0, cfg, {"ltcg_by_fy": {}},
                              None, [], {"friction": 0.0, "taxes": 0.0},
                              monthly_inflow=200000.0)
    assert sum(st.invested for st in states2) == 0.0
    assert cash2 == 200000.0




def test_cash_never_negative(tmp_path, monkeypatch):
    res = _stage4_engine_res(tmp_path, monkeypatch)
    assert all(float(d['cash']) >= 0.0 for d in res['daily_curve'])
    assert all(float(m['cash']) >= 0.0 for m in res['monthly_curve'])
    assert float(res['scorecard']['final_cash']) >= 0.0


def test_uninvested_cash_zero_yield():
    from performance import stage4_backtest as s4b
    assert s4b.CASH_RATE_PA == 0.0


def test_nifty500_calendar_year_returns_populated(tmp_path, monkeypatch):
    res = _stage4_engine_res(tmp_path, monkeypatch)
    cy = res['benchmarks']['nifty500']['calendar_year_returns']
    assert cy, 'Nifty 500 TRI calendar-year returns must not be empty'
    assert all(v is not None for v in cy.values())


# --------------------------------------------------------------------------- #
# Refined LAGGARD_EXIT: >= 730 days, below 200-DMA, >= 15pp lag vs Nifty 500.
# --------------------------------------------------------------------------- #
def _lag_state(first_px, held_days, d, qty=10.0):
    from datetime import timedelta

    from performance.stage4_backtest import _Lot, _StockState

    lot_date = d - timedelta(days=held_days)
    st = _StockState(ticker="LAG.NS", isin=None, first_buy_date=lot_date,
                     sources={"active"})
    st.lots = [_Lot(qty=qty, price=first_px, date=lot_date)]
    return st


def test_laggard_exit_gate_requires_two_years_below_200dma_and_15pp_lag():
    """LAGGARD_EXIT fires only when held >= 730 days, below the 200-DMA AND
    lagging Nifty 500 by >= 15pp over the holding period."""
    from datetime import date

    from performance.stage4_backtest import _laggard_exit_allowed

    d = date(2025, 6, 1)
    prices = _StubPrices({"^CRSLDX": [
        (date(2022, 1, 1), 100.0),
        (d, 200.0),  # Nifty 500 +100% over the window
    ]})
    row = {"dma_200": 120.0}

    # Young position (< 730 days) with a huge lag -> never liquidated.
    young = _lag_state(first_px=100.0, held_days=400, d=d)
    assert _laggard_exit_allowed(young, row, 60.0, prices, d) is False

    # 2 years held, below 200-DMA, +100% Nifty vs -40% stock -> ~140pp lag -> exit.
    old = _lag_state(first_px=100.0, held_days=800, d=d)
    assert _laggard_exit_allowed(old, row, 60.0, prices, d) is True

    # Above the 200-DMA -> hold even with a big lag.
    assert _laggard_exit_allowed(old, {"dma_200": 50.0}, 60.0, prices, d) is False

    # Kept pace with Nifty (+90% stock vs +100% Nifty = 10pp lag) -> hold.
    assert _laggard_exit_allowed(old, row, 190.0, prices, d) is False

    # Exactly 730 days is enough (2 full years).
    edge = _lag_state(first_px=100.0, held_days=730, d=d)
    assert _laggard_exit_allowed(edge, row, 60.0, prices, d) is True


def test_laggard_exit_gate_falls_back_to_monifty500_when_crsldx_missing():
    """^CRSLDX unavailable -> MONIFTY500.NS fallback still measures the lag."""
    from datetime import date

    from performance.stage4_backtest import _laggard_exit_allowed, _nifty500_price

    d = date(2025, 6, 1)
    prices = _StubPrices({"MONIFTY500.NS": [
        (date(2022, 1, 1), 100.0),
        (d, 200.0),
    ]})
    assert _nifty500_price(prices, d) == 200.0
    old = _lag_state(first_px=100.0, held_days=800, d=d)
    assert _laggard_exit_allowed(old, {"dma_200": 120.0}, 60.0, prices, d) is True


def test_laggard_exit_gate_holds_when_index_unavailable():
    """No Nifty 500 data -> lag unmeasurable -> hold, never a blind exit."""
    from datetime import date

    from performance.stage4_backtest import _laggard_exit_allowed

    d = date(2025, 6, 1)
    prices = _StubPrices({})
    old = _lag_state(first_px=100.0, held_days=800, d=d)
    assert _laggard_exit_allowed(old, {"dma_200": 120.0}, 60.0, prices, d) is False


def test_monthly_step_refined_laggard_exit_holds_young_positions(monkeypatch):
    """A young (<730-day) LAGGARD_EXIT signal is NOT liquidated by the engine,
    while a true 2-year laggard is exited."""
    from datetime import date, timedelta

    from performance import stage4_backtest as s4b
    from performance.stage4_signals import LAGGARD_EXIT

    d = date(2025, 6, 1)
    series = [(d - timedelta(days=799 - i), 100.0) for i in range(800)]
    prices = _StubPrices({
        "LAG.NS": series[-260:],
        "^CRSLDX": [(d - timedelta(days=800), 100.0), (d, 200.0)],
    })
    cfg = s4b.BacktestConfig(base_unit_sip=10000.0, max_monthly_ceiling=200000.0)

    def _rows(stocks, totals, bx, as_of_date=None, **kw):
        return [{"security_id": s.security_id, "signal_type": LAGGARD_EXIT,
                 "cmp": 60.0, "dma_200": 120.0, "sip_multiplier": 0.0,
                 "incubation": False} for s in stocks]

    monkeypatch.setattr(s4b, "evaluate_signals", _rows)
    monkeypatch.setattr(
        s4b, "compute_bottom_up_sip_allocation",
        lambda *a, **k: {"rows": [], "total_allocated": 0.0,
                         "natural_total_sip": 0.0, "parked_cash": 0.0,
                         "scaled_down": False})

    def _run(held_days):
        st = _lag_state(first_px=100.0, held_days=held_days, d=d)
        st.activation_date = series[0][0]
        stats = {"friction": 0.0, "taxes": 0.0, "sells": {}}
        cash = s4b._monthly_step([st], prices, d, 0.0, cfg, {"ltcg_by_fy": {}},
                                 None, [], stats, monthly_inflow=0.0)
        return st, cash, stats

    young, cash_young, stats_young = _run(400)
    assert young.qty > 0 and cash_young == 0.0
    assert "LAGGARD_EXIT" not in stats_young["sells"]

    old, cash_old, stats_old = _run(800)
    assert old.qty == 0.0 and cash_old > 0.0
    assert stats_old["sells"]["LAGGARD_EXIT"]["count"] == 1


# --------------------------------------------------------------------------- #
# CATASTROPHE_GTT disabled: no blind 45% liquidation and no permanent ban.
# --------------------------------------------------------------------------- #
def test_intraday_gtt_no_blind_catastrophe_liquidation():
    """A >45% drawdown no longer auto-liquidates or permanently bans the stock;
    only the signal-based GTT_STOP_LOSS may exit."""
    from datetime import date

    from performance import stage4_backtest as s4b

    d = date(2025, 6, 1)
    prices = _StubPrices({"CRASH.NS": [(date(2025, 1, 1), 100.0), (d, 50.0)]})
    st = s4b._StockState(ticker="CRASH.NS", isin=None,
                         first_buy_date=date(2025, 1, 1), sources={"active"})
    st.activation_date = date(2025, 1, 2)
    st.lots = [s4b._Lot(qty=10.0, price=100.0, date=date(2025, 1, 2))]
    st.peak_price = 100.0  # 50 is 50% below peak -> would have tripped the old floor
    st.gtt = None
    stats = {"friction": 0.0, "taxes": 0.0, "sells": {}}

    cash = s4b._intraday_gtt([st], prices, d, 0.0, {"ltcg_by_fy": {}}, stats)

    assert cash == 0.0
    assert st.qty == 10.0                    # never liquidated
    assert st.permanent_exited is False      # no permanent ban
    assert st.frozen is False
    assert stats["sells"] == {}              # no sell recorded
    assert st.peak_price == 100.0


def test_intraday_gtt_monthly_only_no_sell_on_breach():
    """Stage 4 Exp #6 Variant B (kept): GTT_STOP_LOSS is now monthly-only.

    The daily intraday tick no longer executes the GTT stop; it only tracks the
    running high-water peak. A stock whose price breached its GTT level (drawdown
    > Nifty 500's by >= 20pp) is simply held until the next monthly review, where
    ``_monthly_step`` may exit at that review-day market price.
    """
    from datetime import date

    from performance import stage4_backtest as s4b

    d = date(2025, 6, 1)
    prices = _StubPrices({
        "STOP.NS": [(date(2025, 1, 1), 100.0), (d, 50.0)],   # stock -50%
        "^CRSLDX": [(date(2025, 1, 1), 100.0), (d, 100.0)],  # Nifty flat -> 50pp excess
    })
    st = s4b._StockState(ticker="STOP.NS", isin=None,
                         first_buy_date=date(2025, 1, 1), sources={"active"})
    st.activation_date = date(2025, 1, 2)
    st.lots = [s4b._Lot(qty=10.0, price=100.0, date=date(2025, 1, 2))]
    st.peak_price = 100.0
    st.gtt = 60.0                            # current 50 breaches the dynamic stop
    stats = {"friction": 0.0, "taxes": 0.0, "sells": {}}
    rd = {"nifty500_peak": 100.0}

    cash = s4b._intraday_gtt([st], prices, d, 0.0, {"ltcg_by_fy": {}}, stats, rd=rd)

    assert cash == 0.0
    assert st.qty == 10.0                   # held on the daily tick: no intraday GTT sell
    assert st.frozen is False and st.cooldown_until is None
    assert stats["sells"] == {}             # no sell recorded
    assert st.peak_price == 100.0           # peak still tracked for drawdown math


def test_intraday_gtt_ignores_broad_market_pullback():
    """Stock and Nifty 500 fall together (same drawdown) -> GTT_STOP_LOSS is
    blocked even though the trailing-stop price is breached."""
    from datetime import date

    from performance import stage4_backtest as s4b

    d = date(2025, 6, 1)
    prices = _StubPrices({
        "MKT.NS": [(date(2025, 1, 1), 100.0), (d, 50.0)],    # stock -50%
        "^CRSLDX": [(date(2025, 1, 1), 100.0), (d, 50.0)],   # Nifty -50% -> 0pp excess
    })
    st = s4b._StockState(ticker="MKT.NS", isin=None,
                         first_buy_date=date(2025, 1, 1), sources={"active"})
    st.activation_date = date(2025, 1, 2)
    st.lots = [s4b._Lot(qty=10.0, price=100.0, date=date(2025, 1, 2))]
    st.peak_price = 100.0
    st.gtt = 60.0                            # price stop breached, but market-wide
    stats = {"friction": 0.0, "taxes": 0.0, "sells": {}}
    rd = {"nifty500_peak": 100.0}

    cash = s4b._intraday_gtt([st], prices, d, 0.0, {"ltcg_by_fy": {}}, stats, rd=rd)

    assert cash == 0.0
    assert st.qty == 10.0 and st.frozen is False
    assert stats["sells"] == {}


def test_monthly_gtt_stop_fires_at_px_and_redeploys_cash(monkeypatch):
    """Stage 4 Exp #6 Variant B (kept): monthly-only GTT_STOP_LOSS execution.

    On the monthly review day the engine sells when the stock's price has fallen
    to/below its dynamic GTT level AND the stock's drawdown exceeds Nifty 500's by
    >= GTT_STOP_LOSS_MIN_NIFTY_LAG. The exit executes at the review-day market
    price (cmp) — not the historical trigger level — and the freed proceeds are
    redeployed into the same month's bottom-up SIP allocation (same step).
    """
    from datetime import date, timedelta

    from performance import stage4_backtest as s4b
    from performance.stage4_backtest import BacktestConfig, _Lot, _StockState

    d = date(2025, 6, 1)
    series_gtt = [(d - timedelta(days=259 - i), 100.0) for i in range(260)]
    series_gtt[-1] = (d, 50.0)  # GTT.NS crashes to 50 on the review day
    series_ok = [(d - timedelta(days=259 - i), 100.0) for i in range(260)]
    series_ok[-1] = (d, 100.0)  # OK.NS stays flat / healthy
    prices = _StubPrices({
        "GTT.NS": series_gtt,
        "OK.NS": series_ok,
        "^CRSLDX": [(d - timedelta(days=800), 100.0), (d, 100.0)],  # Nifty flat
    })
    cfg = BacktestConfig(base_unit_sip=10000.0, max_monthly_ceiling=200000.0)

    def _rows(stocks, totals, bx, as_of_date=None, **kw):
        return [
            {"security_id": "GTT.NS", "signal_type": "NO_SIGNAL",
             "cmp": 50.0, "gtt_stop_loss_price": 60.0, "sip_multiplier": 0.0,
             "incubation": False},
            {"security_id": "OK.NS", "signal_type": "NO_SIGNAL",
             "cmp": 100.0, "gtt_stop_loss_price": None, "sip_multiplier": 2.5,
             "incubation": False},
        ]

    monkeypatch.setattr(s4b, "evaluate_signals", _rows)
    monkeypatch.setattr(
        s4b, "compute_bottom_up_sip_allocation",
        lambda *a, **k: {"rows": [
            {"security_id": "OK.NS", "recommended_sip_rupees": 25000.0},
        ], "total_allocated": 25000.0, "natural_total_sip": 25000.0,
            "parked_cash": 0.0, "scaled_down": False})

    st_gtt = _StockState(ticker="GTT.NS", isin=None, first_buy_date=date(2025, 1, 2),
                         sources={"active"})
    st_gtt.activation_date = date(2025, 1, 2)
    st_gtt.lots = [_Lot(qty=10.0, price=100.0, date=date(2025, 1, 2))]
    st_gtt.peak_price = 100.0
    st_gtt.gtt = 60.0

    st_ok = _StockState(ticker="OK.NS", isin=None, first_buy_date=date(2025, 1, 2),
                        sources={"active"})
    st_ok.activation_date = date(2025, 1, 2)
    st_ok.lots = [_Lot(qty=10.0, price=100.0, date=date(2025, 1, 2))]
    st_ok.peak_price = 100.0
    st_ok.gtt = None

    stats = {"friction": 0.0, "taxes": 0.0, "sells": {}}
    rd = {"pending": 0.0, "release": {}, "nifty500_peak": 100.0}
    s4b._monthly_step([st_gtt, st_ok], prices, d, 0.0, cfg,
                      {"ltcg_by_fy": {}}, None, [], stats,
                      monthly_inflow=0.0, rd=rd)

    # GTT breach -> monthly-only sell executed at the review-day market price (cmp).
    assert st_gtt.qty == 0.0
    assert stats["sells"]["GTT_STOP_LOSS"]["count"] == 1
    assert st_gtt.gross_proceeds == 500.0  # 10 sh * 50.0 (cmp) -> exit at review-day px

    # The freed cash is redeployed into the same month's SIP allocation.
    assert st_ok.qty > 10.0


def test_crash_freeze_pauses_gtt_stop_loss_during_market_crash(monkeypatch):
    """Exp #8 (diagnostic): Nifty 500 trailing-52w drawdown < -20% -> market crash
    freeze pauses the defensive GTT_STOP_LOSS sell for that monthly review month."""
    from datetime import date, timedelta

    from performance import stage4_backtest as s4b
    from performance.stage4_backtest import BacktestConfig, _Lot, _StockState

    d = date(2025, 6, 1)
    series_gtt = [(d - timedelta(days=259 - i), 100.0) for i in range(260)]
    series_gtt[-1] = (d, 50.0)  # GTT.NS crashes to 50 on the review day
    prices = _StubPrices({
        "GTT.NS": series_gtt,
        "^CRSLDX": [(d - timedelta(days=300), 100.0), (d, 75.0)],  # Nifty -25% -> crash
    })
    cfg = BacktestConfig(base_unit_sip=10000.0, max_monthly_ceiling=200000.0)

    def _rows(stocks, totals, bx, as_of_date=None, **kw):
        return [
            {"security_id": "GTT.NS", "signal_type": "NO_SIGNAL",
             "cmp": 50.0, "gtt_stop_loss_price": 60.0, "sip_multiplier": 0.0,
             "incubation": False},
        ]

    monkeypatch.setattr(s4b, "evaluate_signals", _rows)
    monkeypatch.setattr(
        s4b, "compute_bottom_up_sip_allocation",
        lambda *a, **k: {"rows": [], "total_allocated": 0.0, "natural_total_sip": 0.0,
                        "parked_cash": 0.0, "scaled_down": False})

    st = _StockState(ticker="GTT.NS", isin=None, first_buy_date=date(2025, 1, 2),
                     sources={"active"})
    st.activation_date = date(2025, 1, 2)
    st.lots = [_Lot(qty=10.0, price=100.0, date=date(2025, 1, 2))]
    st.peak_price = 100.0
    st.gtt = 60.0
    stats = {"friction": 0.0, "taxes": 0.0, "sells": {}}
    rd = {"pending": 0.0, "release": {}, "nifty500_peak": 100.0}

    s4b._monthly_step([st], prices, d, 0.0, cfg, {"ltcg_by_fy": {}},
                      None, [], stats, monthly_inflow=0.0, rd=rd)

    # Nifty 52w drawdown = (75 - 100) / 100 = -0.25 -> crash freeze engaged.
    assert stats["crash_freeze"]["triggered"] == 1
    assert stats["crash_freeze"]["nifty_dd"] == -0.25
    assert st.qty == 10.0                     # held through the crash
    assert stats["sells"] == {}                # no GTT_STOP_LOSS sell recorded
    assert st.frozen is False                 # no cooldown booked on freeze


def test_crash_freeze_pauses_laggard_exit_during_market_crash(monkeypatch):
    """Exp #8 (diagnostic): crash freeze pauses LAGGARD_EXIT during a >=20% crash."""
    from datetime import date, timedelta

    from performance import stage4_backtest as s4b
    from performance.stage4_backtest import BacktestConfig, _Lot, _StockState

    d = date(2025, 6, 1)
    lag_series = [(d - timedelta(days=259 - i), 100.0) for i in range(260)]
    lag_series[-1] = (d, 60.0)  # LAG.NS -40% over the holding period
    prices = _StubPrices({
        "LAG.NS": lag_series,
        "^CRSLDX": [(date(2023, 6, 1), 100.0), (date(2024, 6, 1), 100.0), (d, 75.0)],  # Nifty -25% -> crash
    })
    cfg = BacktestConfig(base_unit_sip=10000.0, max_monthly_ceiling=200000.0)

    def _rows(stocks, totals, bx, as_of_date=None, **kw):
        return [
            {"security_id": "LAG.NS", "signal_type": "LAGGARD_EXIT",
             "cmp": 60.0, "gtt_stop_loss_price": None, "sip_multiplier": 0.0,
             "incubation": False, "dma_200": 80.0},
        ]

    monkeypatch.setattr(s4b, "evaluate_signals", _rows)
    monkeypatch.setattr(
        s4b, "compute_bottom_up_sip_allocation",
        lambda *a, **k: {"rows": [], "total_allocated": 0.0, "natural_total_sip": 0.0,
                        "parked_cash": 0.0, "scaled_down": False})

    st = _StockState(ticker="LAG.NS", isin=None, first_buy_date=date(2023, 6, 1),
                     sources={"active"})
    st.activation_date = date(2023, 6, 1)
    st.lots = [_Lot(qty=10.0, price=100.0, date=date(2023, 6, 1))]
    stats = {"friction": 0.0, "taxes": 0.0, "sells": {}}
    rd = {"pending": 0.0, "release": {}, "nifty500_peak": 100.0}

    s4b._monthly_step([st], prices, d, 0.0, cfg, {"ltcg_by_fy": {}},
                      None, [], stats, monthly_inflow=0.0, rd=rd)

    # LAGGARD would have fired absent the freeze (held 730d+, below 200-DMA,
    # lagging Nifty >=15pp: stock -40% vs Nifty -25% -> 15pp excess lag).
    assert s4b._laggard_exit_allowed(st, {"dma_200": 80.0}, 60.0, prices, d) is True
    assert stats["crash_freeze"]["triggered"] == 1
    assert st.qty == 10.0                     # frozen by crash freeze -> not sold
    assert stats["sells"] == {}


def test_crash_freeze_not_engaged_when_nifty_drawdown_ge_minus_20pct(monkeypatch):
    """Exp #8 control: mild Nifty pullback (-10%) -> no crash freeze -> GTT fires."""
    from datetime import date, timedelta

    from performance import stage4_backtest as s4b
    from performance.stage4_backtest import BacktestConfig, _Lot, _StockState

    d = date(2025, 6, 1)
    series_gtt = [(d - timedelta(days=259 - i), 100.0) for i in range(260)]
    series_gtt[-1] = (d, 50.0)  # GTT.NS crashes to 50 on the review day
    prices = _StubPrices({
        "GTT.NS": series_gtt,
        "^CRSLDX": [(d - timedelta(days=300), 100.0), (d, 90.0)],  # Nifty -10% -> no crash
    })
    cfg = BacktestConfig(base_unit_sip=10000.0, max_monthly_ceiling=200000.0)

    def _rows(stocks, totals, bx, as_of_date=None, **kw):
        return [
            {"security_id": "GTT.NS", "signal_type": "NO_SIGNAL",
             "cmp": 50.0, "gtt_stop_loss_price": 60.0, "sip_multiplier": 0.0,
             "incubation": False},
        ]

    monkeypatch.setattr(s4b, "evaluate_signals", _rows)
    monkeypatch.setattr(
        s4b, "compute_bottom_up_sip_allocation",
        lambda *a, **k: {"rows": [], "total_allocated": 0.0, "natural_total_sip": 0.0,
                        "parked_cash": 0.0, "scaled_down": False})

    st = _StockState(ticker="GTT.NS", isin=None, first_buy_date=date(2025, 1, 2),
                     sources={"active"})
    st.activation_date = date(2025, 1, 2)
    st.lots = [_Lot(qty=10.0, price=100.0, date=date(2025, 1, 2))]
    st.peak_price = 100.0
    st.gtt = 60.0
    stats = {"friction": 0.0, "taxes": 0.0, "sells": {}}
    rd = {"pending": 0.0, "release": {}, "nifty500_peak": 100.0}

    s4b._monthly_step([st], prices, d, 0.0, cfg, {"ltcg_by_fy": {}},
                      None, [], stats, monthly_inflow=0.0, rd=rd)

    # Mild pullback -> no crash freeze -> GTT stop breach fires normally.
    assert stats["crash_freeze"]["triggered"] == 0
    assert st.qty == 0.0
    assert stats["sells"]["GTT_STOP_LOSS"]["count"] == 1
    assert st.frozen is True


def test_gtt_stop_gate_uses_monifty500_fallback_and_holds_if_unmeasurable():
    """^CRSLDX unavailable -> MONIFTY500.NS fallback measures Nifty drawdown;
    with no index data at all the position is held (no blind stop)."""
    from datetime import date

    from performance import stage4_backtest as s4b

    d = date(2025, 6, 1)
    st = s4b._StockState(ticker="X.NS", isin=None,
                         first_buy_date=date(2025, 1, 1), sources={"active"})
    st.peak_price = 100.0
    rd = {"nifty500_peak": 100.0}

    fallback = _StubPrices({"MONIFTY500.NS": [(date(2025, 1, 1), 100.0), (d, 100.0)]})
    assert s4b._gtt_stop_allowed(st, 50.0, fallback, d, rd) is True

    empty = _StubPrices({})
    assert s4b._gtt_stop_allowed(st, 50.0, empty, d, rd) is False


# --------------------------------------------------------------------------- #
# AER = Allocation Expansion Ratio (current_weight / cost_weight).
# --------------------------------------------------------------------------- #
def _aer_stock(sid, invested, mv, series=None, xirr=0.30):
    from performance import stage4_signals as s4

    return s4.StockInput(
        security_id=sid, symbol=sid, quantity=100.0, avg_buy_price=100.0,
        invested_cost=invested, market_value=mv, xirr=xirr,
        price_series=series if series is not None else [100.0] * 260)


def test_aer_doubling_stock_flat_portfolio_is_two():
    from performance import stage4_signals as s4

    stock = _aer_stock("DOUBLE", invested=100000.0, mv=200000.0)
    totals = s4.PortfolioTotals(total_invested_cost=1_000_000.0,
                                total_market_value=1_000_000.0)
    m = s4.analyzeStockSignals(stock, totals, 0.12)
    assert abs(m["aer"] - 2.0) < 1e-9


def test_aer_same_growth_as_portfolio_is_one():
    from performance import stage4_signals as s4

    stock = _aer_stock("SAME", invested=100000.0, mv=150000.0)
    totals = s4.PortfolioTotals(total_invested_cost=1_000_000.0,
                                total_market_value=1_500_000.0)  # +50% like the stock
    m = s4.analyzeStockSignals(stock, totals, 0.12)
    assert abs(m["aer"] - 1.0) < 1e-9


def test_aer_stock_drops_50pct_flat_portfolio_is_half():
    from performance import stage4_signals as s4

    stock = _aer_stock("DROP", invested=100000.0, mv=50000.0, xirr=-0.20)
    totals = s4.PortfolioTotals(total_invested_cost=1_000_000.0,
                                total_market_value=1_000_000.0)
    m = s4.analyzeStockSignals(stock, totals, 0.12)
    assert abs(m["aer"] - 0.5) < 1e-9


def test_aer_zero_cost_and_empty_portfolio_default_to_one():
    import math

    from performance import stage4_signals as s4

    # Zero-cost stock + empty portfolio.
    zero = _aer_stock("ZC", invested=0.0, mv=0.0)
    m0 = s4.analyzeStockSignals(zero, s4.PortfolioTotals(0.0, 0.0), 0.12)
    assert m0["aer"] == 1.0 and math.isfinite(m0["aer"])

    # Positive cost but zero total market value -> still 1.0 (no division by zero).
    stock = _aer_stock("X", invested=1000.0, mv=0.0)
    m1 = s4.analyzeStockSignals(stock, s4.PortfolioTotals(1000.0, 0.0), 0.12)
    assert m1["aer"] == 1.0 and math.isfinite(m1["aer"])

    # Zero total invested cost (e.g. only zero-cost names) -> 1.0.
    m2 = s4.analyzeStockSignals(stock, s4.PortfolioTotals(0.0, 1_000_000.0), 0.12)
    assert m2["aer"] == 1.0 and math.isfinite(m2["aer"])


def test_aer_two_x_triggers_concentration_trim():
    from performance import stage4_signals as s4

    # AER = 2.3 (> TRIM_AER = 2.20), market value >= Rs 2L and XIRR 30% -> TRIM fires.
    stock = _aer_stock("BIG", invested=100000.0, mv=230000.0, xirr=0.30)
    totals = s4.PortfolioTotals(total_invested_cost=1_000_000.0,
                                total_market_value=1_000_000.0)
    m = s4.analyzeStockSignals(stock, totals, 0.12)
    assert abs(m["aer"] - 2.3) < 1e-9
    sig = s4.evaluate_signal(stock, m, totals)
    assert sig.signal_type == s4.CONCENTRATION_TRIM


def test_zero_cost_stock_cannot_pass_lane_two():
    """Zero-cost / closed stock with aer defaulting to 1.0 must NOT get a Lane-2
    (1.0x) SIP from compute_sip_multiplier."""
    from performance import stage4_signals as s4

    stock = _aer_stock("NEW", invested=0.0, mv=0.0, series=[100.0] * 260)
    totals = s4.PortfolioTotals(total_invested_cost=1_000_000.0,
                                total_market_value=1_000_000.0)
    m = s4.analyzeStockSignals(stock, totals, 0.12)
    assert m["aer"] == 1.0  # default applied
    # Even though 0.85 <= aer < 1.25 and DMA distance is 0, zero-cost is forced 0.0x.
    metrics = {"cmp": 100.0, "dma_200": 90.0, "aer": m["aer"]}
    assert s4.compute_sip_multiplier(stock, metrics, 0.12) == 0.0

def test_sip_multiplier_zero_below_200dma_non_incubating():
    """Non-incubating holding below 200-DMA gets 0.0x SIP (no averaging down)."""
    stock = _aer_stock("BELOW", invested=150000.0, mv=100000.0, series=[90.0] * 260)
    metrics = {"cmp": 90.0, "dma_200": 100.0, "aer": 1.0}
    # dma200_distance = (90 - 100) / 100 = -0.10 (< 0) -> 0.0x
    assert s4.compute_sip_multiplier(stock, metrics, 0.12) == 0.0

# --------------------------------------------------------------------------- #
# Experiment #5: budget ceiling removed — recycled sell proceeds deploy in-full.
# --------------------------------------------------------------------------- #
def test_monthly_step_deploys_recycled_proceeds_beyond_ceiling(monkeypatch):
    """With ₹5L cash and a ₹2L ceiling, the next monthly step deploys ~₹5L
    (not ₹2L) across qualifying stocks — recycled proceeds are not stranded."""
    from datetime import date, timedelta

    from performance import stage4_backtest as s4b
    start = date(2020, 1, 1)
    series = [(start + timedelta(days=i), 100.0) for i in range(260)]
    names = ("A.NS", "B.NS", "C.NS", "D.NS")
    prices = _StubPrices({t: series for t in names})
    d = series[-1][0]
    cfg = s4b.BacktestConfig(base_unit_sip=10000.0, max_monthly_ceiling=200000.0)
    def _make_states():
        sts = []
        for t in names:
            st = s4b._StockState(ticker=t, isin=None, first_buy_date=start,
                                 sources={"active"})
            st.activation_date = start
            sts.append(st)
        return sts
    def _eval_at(mult):
        def _fake(stocks, totals, bx, as_of_date=None, **kwargs):
            return [{"security_id": si.security_id, "sip_multiplier": mult,
                     "cmp": 100.0, "incubation": False, "dma_50": 90.0,
                     "dma_200": 80.0} for si in stocks]
        return _fake
    monkeypatch.setattr(s4b, "evaluate_signals", _eval_at(1.0))
    states = _make_states()
    # Simulate a ₹5L exit landed in cash this month; no fresh inflow.
    cash = s4b._monthly_step(states, prices, d, 500000.0, cfg, {"ltcg_by_fy": {}},
                             None, [], {"friction": 0.0, "taxes": 0.0},
                             monthly_inflow=0.0)
    deployed = sum(st.invested for st in states)
    # Recycled proceeds deploy in full (~₹5L), not capped at ₹2L ceiling.
    assert deployed >= 0.95 * 500000.0, f"deployed {deployed:,.0f}, expected ~5,00,000"
    assert cash <= 0.05 * 500000.0, f"idle cash {cash:,.0f} too high"

def test_monthly_step_cash_never_negative_after_friction(monkeypatch):
    """Cash can never go negative, even if the allocator asks for more than
    cash allows (0.25% friction guardrail holds)."""
    from datetime import date, timedelta

    from performance import stage4_backtest as s4b
    start = date(2020, 1, 1)
    series = [(start + timedelta(days=i), 100.0) for i in range(260)]
    names = ("A.NS", "B.NS", "C.NS", "D.NS")
    prices = _StubPrices({t: series for t in names})
    d = series[-1][0]
    cfg = s4b.BacktestConfig(base_unit_sip=10000.0, max_monthly_ceiling=200000.0)
    def _make_states():
        sts = []
        for t in names:
            st = s4b._StockState(ticker=t, isin=None, first_buy_date=start,
                                 sources={"active"})
            st.activation_date = start
            sts.append(st)
        return sts
    def _eval_at(mult):
        def _fake(stocks, totals, bx, as_of_date=None, **kwargs):
            return [{"security_id": si.security_id, "sip_multiplier": mult,
                     "cmp": 100.0, "incubation": False, "dma_50": 90.0,
                     "dma_200": 80.0} for si in stocks]
        return _fake
    monkeypatch.setattr(s4b, "evaluate_signals", _eval_at(1.0))
    states = _make_states()
    # Only ₹1,000 cash; allocator would want to deploy lakhs — friction guardrail
    # must clamp each buy so cash never goes negative.
    cash = s4b._monthly_step(states, prices, d, 1000.0, cfg, {"ltcg_by_fy": {}},
                             None, [], {"friction": 0.0025, "taxes": 0.0},
                             monthly_inflow=0.0)
    assert cash >= 0.0, f"cash went negative: {cash:,.2f}"
    deployed = sum(st.invested for st in states)
    assert deployed <= 1000.0 + 1e-6, f"deployed {deployed:,.0f} > ₹1000 cash"

