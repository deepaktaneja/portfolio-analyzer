"""Stage 4E-1: Bulk Historical Price Cache & Backtest Universe Loader.

Builds the backtest universe from:
  1. Active equity holdings (quantity > 0)
  2. Saved ``watchlist_stocks`` (persisted incubation watchlist)
  3. Closed historical equity positions (net_qty == 0, e.g. YESBANK.NS)
  4. Benchmarks: Nifty 50 (^NSEI, NIFTYBEES.NS fallback) and Nifty 500 (^CRSLDX, MONIFTY500.NS fallback)

Fetches up to 8 years of daily Adjusted Close in a single bulk
``yf.download(..., period='8y', group_by='ticker')`` call for tickers that are
not already cached in the local ``stock_prices`` table, then persists them so
subsequent runs read from the local DB in well under a second with zero API
calls. Eight years covers a 7-year backtest plus the ~200-trading-day warm-up
needed for the 200-DMA (the same cache also serves 3- and 5-year runs). Delisted
tickers (empty data) are skipped and logged; IPO tickers record their first
valid trade date so they only enter the simulation after enough post-listing
history exists. Closed positions also carry their first-buy date (``min`` of
their buy ``trade_date`` values) so the simulator knows when the position
opened, falling back to the backtest start date when unavailable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import pandas as pd

from market_data.prices import is_isin, normalize_security_id
from market_data.repository import MarketDataStore

try:  # optional dependency, imported lazily-friendly but patchable in tests
    import yfinance as yf
except Exception:  # noqa: BLE001
    yf = None

DEFAULT_MARKET_DB = os.environ.get("ICICI_MARKET_DATA_DB_PATH", "data/market_data.sqlite3")
NIFTY50_TICKER = "^NSEI"                 # primary Nifty 50 index (true TRI proxy)
NIFTY50_FALLBACK = "NIFTYBEES.NS"        # ETF fallback when ^NSEI has no data
NIFTY500_TICKERS = ("^CRSLDX", "MONIFTY500.NS")  # primary index, ETF fallback
BENCHMARK_TICKERS = (NIFTY50_TICKER, NIFTY500_TICKERS[0])
BENCHMARK_FALLBACKS = {
    NIFTY50_TICKER: NIFTY50_FALLBACK,      # ^NSEI -> NIFTYBEES.NS
    NIFTY500_TICKERS[0]: NIFTY500_TICKERS[1],  # ^CRSLDX -> MONIFTY500.NS
}
BENCHMARK_ETFS = BENCHMARK_TICKERS  # backwards-compatible alias
EXCLUDED_TICKERS = frozenset({"MUFNDF"})  # muted / non-equity tickers to ignore

# Closed holdings sometimes store company names instead of tickers (e.g. from
# broker exports). Map those to canonical NSE tickers so they load from the
# price cache instead of being skipped as 'no data'.
COMPANY_NAME_TO_TICKER = {
    "AXIS BANK": "AXISBANK.NS",
    "AXIS BANK LIMITED": "AXISBANK.NS",
    "ICICI BANK": "ICICIBANK.NS",
    "ICICI BANK L": "ICICIBANK.NS",
    "ICICI BANK LTD": "ICICIBANK.NS",
    "INFOSYS": "INFY.NS",
    "INFOSYS LIMITED": "INFY.NS",
    "HINDUSTAN ZINC": "HINDZINC.NS",
    "HINDUSTAN ZINC LIMITED": "HINDZINC.NS",
    "OIL AND NATURAL GAS CORP": "ONGC.NS",
    "POWER FIN CORP": "PFC.NS",
    "POWER FINAN": "PFC.NS",
    "TATA CHEMICALS": "TATACHEM.NS",
    "TATA CHEMICALS LTD": "TATACHEM.NS",
    "BANK OF BARODA": "BANKBARODA.NS",
    "BANK OF BARODA-PARI PASSU": "BANKBARODA.NS",
    "RAIN INDUSTRIES": "RAIN.NS",
    "RAIN INDUSTRIES LIMI": "RAIN.NS",
}
FETCH_PERIOD = "8y"
IPO_MIN_TRADING_DAYS = 200
PRICE_SOURCE = "yfinance"


def _default_db(market_db_path=None) -> str:
    return str(market_db_path or DEFAULT_MARKET_DB)


def _is_excluded(ticker) -> bool:
    base = str(ticker or "").split(".")[0].strip().upper()
    return base in EXCLUDED_TICKERS


def _norm_ticker(ticker) -> str:
    """Return an NSE yfinance-style ticker (``SJS`` -> ``SJS.NS``)."""
    t = str(ticker or "").strip().upper()
    if not t:
        return ""
    if t.startswith("^") or "." in t:
        return t
    return t + ".NS"


def _net_qty_closed_isins(txs) -> list[str]:
    """ISINs fully closed out (net quantity == 0 after buys and sells)."""
    net: dict[str, float] = {}
    bought: set[str] = set()
    for t in txs or []:
        isin = str(t.get("isin") or t.get("security") or "").strip().upper()
        if not isin:
            continue
        side = str(t.get("buy_sell") or t.get("side") or "").strip().upper()
        try:
            qty = float(t.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if side == "BUY":
            net[isin] = net.get(isin, 0.0) + qty
            bought.add(isin)
        elif side == "SELL":
            net[isin] = net.get(isin, 0.0) - qty
    return [isin for isin in bought if abs(net.get(isin, 0.0)) <= 1e-6]


def _first_buy_dates(txs) -> dict[str, str]:
    """Earliest buy/open ``trade_date`` per ISIN (``{isin: 'YYYY-MM-DD'}``).

    Read straight from the real transaction ledger (the ``txs`` list assembled
    from the ``transactions`` table) so a closed position's first-buy date is the
    true historical entry, not a synthetic date. Rows without a usable date are
    ignored; callers apply their own fallback when an ISIN is absent here.
    """
    out: dict[str, str] = {}
    for t in txs or []:
        side = str(t.get("buy_sell") or t.get("side") or "").strip().upper()
        if side not in ("BUY", "OPENING"):
            continue
        isin = str(t.get("isin") or t.get("security") or "").strip().upper()
        if not isin:
            continue
        raw_date = t.get("trade_date") or t.get("date") or t.get("settlement_date")
        if not raw_date:
            continue
        ds = str(raw_date)[:10]
        if isin not in out or ds < out[isin]:
            out[isin] = ds
    return out


def _add_entry(universe: dict, db: str, isin=None, hint=None, force_ticker=None) -> None:
    """Resolve an isin/ticker hint to a canonical NSE ticker and add to the universe."""
    isin = str(isin).strip().upper() if isin else None
    hint = str(hint).strip().upper() if hint else None
    ident = normalize_security_id(isin or hint or force_ticker or "", db_path=db)
    hint_ticker = _norm_ticker(hint) if (hint and not is_isin(hint)) else None
    ticker = force_ticker or ident.get("nse_ticker") or hint_ticker
    # Fallback: map company-name strings (with/without .NS) to canonical NSE
    # tickers so closed holdings stored by company name are not skipped.
    for _cand in (hint, isin, ticker):
        if not _cand:
            continue
        _base = _cand.removesuffix(".NS")
        if _base in COMPANY_NAME_TO_TICKER:
            ticker = COMPANY_NAME_TO_TICKER[_base]
            break
    if not ticker:
        return
    ticker = _norm_ticker(ticker)
    if _is_excluded(ticker):
        return
    universe[ticker] = isin or ident.get("isin")


def build_universe_tickers(ctx=None, market_db_path=None, txs=None, holdings=None) -> dict:
    """Return ``{nse_ticker: isin_or_None}`` for the full backtest universe."""
    db = _default_db(market_db_path)
    store = MarketDataStore(db)
    universe: dict = {}

    # 1. Active equity holdings (quantity > 0)
    h = holdings if holdings is not None else getattr(ctx, "holdings", None)
    if h is not None and hasattr(h, "iterrows"):
        try:
            for _, r in h.iterrows():
                try:
                    qty = float(r.get("quantity") or 0)
                except (TypeError, ValueError):
                    qty = 0.0
                if qty <= 1e-6:
                    continue
                _add_entry(universe, db, isin=r.get("isin"), hint=r.get("symbol"))
        except Exception:  # noqa: BLE001, S110
            pass

    # 2. Saved watchlist stocks (persisted)
    try:
        for row in store.get_watchlist_stocks():
            _add_entry(universe, db, isin=row.get("isin"), hint=row.get("nse_ticker"))
    except Exception:  # noqa: BLE001, S110
        pass

    # 3. Closed historical positions (net_qty == 0)
    t = txs if txs is not None else getattr(ctx, "txs", None)
    for isin in _net_qty_closed_isins(t or []):
        _add_entry(universe, db, isin=isin)

    # 4. Benchmark ETFs
    for etf in BENCHMARK_ETFS:
        _add_entry(universe, db, hint=etf, force_ticker=etf)

    return dict(sorted(universe.items()))


@dataclass
class UniverseResult:
    """Outcome of a bulk cache load (all reads served from the local DB)."""

    tickers: list[str] = field(default_factory=list)
    isin_by_ticker: dict = field(default_factory=dict)
    first_valid_date: dict = field(default_factory=dict)
    trading_days: dict = field(default_factory=dict)
    cached_tickers: list[str] = field(default_factory=list)
    newly_fetched: list[str] = field(default_factory=list)
    skipped_tickers: list[str] = field(default_factory=list)
    fetch_summary: dict = field(default_factory=dict)
    first_buy_date: dict = field(default_factory=dict)

    @property
    def first_valid_trade_date(self) -> dict:
        """Alias matching the Stage 4E spec terminology."""
        return self.first_valid_date


def _extract_adj_close(df, ticker) -> list[tuple[str, float]]:
    """Extract an ascending ``[(iso_date, adj_close)]`` series for one ticker.

    Handles both ``group_by='ticker'`` MultiIndex frames and single-ticker flat
    frames. Returns ``[]`` when the ticker is absent or has no usable data
    (e.g. a delisted symbol).
    """
    if df is None or len(df) == 0:
        return []
    sub = None
    if isinstance(df.columns, pd.MultiIndex):
        level0 = set(map(str, df.columns.get_level_values(0)))
        level1 = set(map(str, df.columns.get_level_values(1)))
        if ticker in level0:
            sub = df[ticker]
        elif ticker in level1:
            sub = df.xs(ticker, axis=1, level=1)
    elif ticker in df.columns:
        sub = df[ticker]
    else:
        # single-ticker flat frame (columns are fields, not tickers)
        sub = df
    if sub is None or not hasattr(sub, "columns"):
        return []
    field_name = None
    for cand in ("Adj Close", "AdjClose", "Close"):
        if cand in sub.columns:
            field_name = cand
            break
    if field_name is None:
        return []
    series = pd.to_numeric(sub[field_name], errors="coerce").dropna()
    out: list[tuple[str, float]] = []
    for idx, val in series.items():
        try:
            px = float(val)
        except (TypeError, ValueError):
            continue
        if pd.isna(px):  # NaN
            continue
        d = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        out.append((d, px))
    out.sort(key=lambda x: x[0])
    return out


def _bulk_download(tickers, period=FETCH_PERIOD):
    """One yf.download call for every uncached ticker."""
    if yf is None:
        raise RuntimeError("yfinance is not available")
    return yf.download(list(tickers), period=period, group_by="ticker",
                       progress=False, auto_adjust=False)


def _cached_count(store: MarketDataStore, ticker) -> int:
    try:
        with store._conn() as c:
            row = c.execute("SELECT COUNT(*) FROM stock_prices WHERE security_id=?",
                            (ticker,)).fetchone()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def _ticker_range(store: MarketDataStore, ticker):
    try:
        with store._conn() as c:
            row = c.execute(
                "SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM stock_prices "
                "WHERE security_id=?", (ticker,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row or not row[2]:
        return None
    return {"first": row[0], "last": row[1], "count": int(row[2])}


def load_backtest_universe(ctx=None, market_db_path=None, txs=None, holdings=None,
                           force_refresh=False, period=FETCH_PERIOD,
                           backtest_start=None) -> UniverseResult:
    """Build the universe and bulk-cache any tickers missing from the local DB.

    First run fetches all uncached tickers in ONE ``yf.download`` call and
    persists them. Later runs make zero external API calls (everything cached).

    ``backtest_start`` is the fallback first-buy date for closed positions whose
    transactions carry no usable date (defaults to the earliest cached date).
    """
    db = _default_db(market_db_path)
    store = MarketDataStore(db)
    universe = build_universe_tickers(ctx, db, txs=txs, holdings=holdings)

    if force_refresh:
        store.clear_skipped_tickers(list(universe))
    try:
        already_skipped = {r["ticker"] for r in store.get_skipped_tickers()}
    except Exception:  # noqa: BLE001
        already_skipped = set()

    cached, to_fetch = [], []
    skipped: list[str] = []
    for ticker, isin in universe.items():
        if not force_refresh and ticker in already_skipped:
            skipped.append(ticker)  # delisted previously -> don't retry (zero API)
            continue
        has_cache = _cached_count(store, ticker) > 0 or (
            bool(isin) and _cached_count(store, isin) > 0)
        if not force_refresh and has_cache:
            cached.append(ticker)
        else:
            to_fetch.append(ticker)

    newly_fetched: list[str] = []
    newly_skipped: list[str] = []
    downloadable = list(to_fetch)  # benchmark indices (e.g. ^CRSLDX) are downloadable too

    def _price_rows(sec_id, series):
        return [
            {"security_id": sec_id, "exchange": "NSE", "trade_date": d,
             "close_price": px, "adjusted_close": px, "raw_value": px,
             "price_type": "adjusted", "source": PRICE_SOURCE,
             "provider": PRICE_SOURCE, "verification_status": "unverified"}
            for d, px in series
        ]

    if downloadable:
        try:
            df = _bulk_download(downloadable, period=period)
        except Exception:  # noqa: BLE001
            df = None
        for ticker in downloadable:
            series = _extract_adj_close(df, ticker)
            if not series:
                # Benchmark index fallback (e.g. ^CRSLDX -> MONIFTY500.NS).
                fb = BENCHMARK_FALLBACKS.get(ticker)
                if fb:
                    try:
                        fdf = _bulk_download([fb], period=period)
                    except Exception:  # noqa: BLE001
                        fdf = None
                    fseries = _extract_adj_close(fdf, fb)
                    if fseries:
                        store.upsert_stock_prices(_price_rows(fb, fseries))
                        newly_fetched.append(fb)
                        continue
                skipped.append(ticker)  # delisted / no data -> skip cleanly
                newly_skipped.append(ticker)
                continue
            store.upsert_stock_prices(_price_rows(ticker, series))
            newly_fetched.append(ticker)
        if newly_skipped:
            store.mark_skipped_tickers(newly_skipped, reason="no_data")

    result = UniverseResult()
    result.tickers = sorted(universe)
    result.isin_by_ticker = dict(universe)
    result.cached_tickers = sorted(cached)
    result.newly_fetched = sorted(newly_fetched)
    result.skipped_tickers = sorted(set(skipped))
    for ticker in result.tickers:
        rng = _ticker_range(store, ticker)
        if rng:
            result.first_valid_date[ticker] = rng["first"]
            result.trading_days[ticker] = rng["count"]

    # ALL holdings (both currently open and closed) carry their true discovery
    # date = min buy trade_date from the real transactions ledger. Each existing
    # stock therefore stays completely invisible in the simulation until the day
    # it was actually first bought in real life (eliminating hindsight bias).
    # If the transactions carry no dates, fall back to the backtest start date,
    # or the earliest cached date in the universe.
    _txs = txs if txs is not None else getattr(ctx, "txs", None)
    closed_isins = set(_net_qty_closed_isins(_txs or []))
    first_buys = _first_buy_dates(_txs or [])
    fallback_start = (str(backtest_start)[:10] if backtest_start
                      else (min(result.first_valid_date.values())
                            if result.first_valid_date else None))
    for ticker, isin in universe.items():
        if isin and isin in first_buys:
            result.first_buy_date[ticker] = first_buys[isin]
        elif isin and isin in closed_isins:
            # Closed position with no usable trade date -> fallback.
            result.first_buy_date[ticker] = fallback_start

    result.fetch_summary = {
        "universe": len(result.tickers),
        "requested": len(to_fetch),
        "fetched": len(newly_fetched),
        "skipped": len(result.skipped_tickers),
        "cached": len(cached),
    }
    return result


def cached_price_series(ticker, start=None, end=None, market_db_path=None):
    """DB-only price read (``[(date, close)]``) — never hits the network."""
    store = MarketDataStore(_default_db(market_db_path))
    lo = str(start)[:10] if start else "0000-01-01"
    hi = str(end)[:10] if end else "9999-12-31"
    return store.get_stock_prices(ticker, lo, hi)


def universe_price_summary(market_db_path=None, tickers=None) -> dict:
    """Per-ticker ``{first, last, count}`` cached range for verification."""
    store = MarketDataStore(_default_db(market_db_path))
    if tickers is None:
        try:
            with store._conn() as c:
                tickers = [r[0] for r in c.execute(
                    "SELECT DISTINCT security_id FROM stock_prices ORDER BY security_id").fetchall()]
        except Exception:  # noqa: BLE001
            tickers = []
    out: dict = {}
    for tk in tickers or []:
        rng = _ticker_range(store, tk)
        if rng:
            out[tk] = rng
    return out


def print_universe_verification(result=None, market_db_path=None, tickers=None) -> dict:
    """Print loaded tickers, their cached date ranges, and skipped tickers.

    Returns the per-ticker ``{first, last, count}`` summary (same shape as
    :func:`universe_price_summary`) so scripts/tests can assert on it.
    """
    if tickers is None and result is not None:
        tickers = list(result.tickers)
    summary = universe_price_summary(market_db_path, tickers=tickers)
    print("Backtest universe verification")
    print("=" * 64)
    for tk in (tickers if tickers is not None else sorted(summary)):
        rng = summary.get(tk)
        if rng:
            print(f"  {tk:<20} {rng['first']} -> {rng['last']}  "
                  f"({rng['count']} trading days)")
        else:
            print(f"  {tk:<20} (no cached data)")
    skipped = sorted(set(result.skipped_tickers)) if result is not None else []
    print("-" * 64)
    print("Skipped (delisted / no data): "
          + (", ".join(skipped) if skipped else "none"))
    if result is not None and result.first_buy_date:
        closed = ", ".join(f"{tk}@{d}" for tk, d in sorted(result.first_buy_date.items()))
        print(f"Closed first-buy dates: {closed}")
    return summary


def is_ipo_ready(ticker, first_valid_date=None, market_db_path=None,
                 min_days=IPO_MIN_TRADING_DAYS) -> bool:
    """True once a ticker has ``min_days`` trading days of history on/after its
    first valid (IPO) trade date — used to gate newer IPOs in the simulation."""
    store = MarketDataStore(_default_db(market_db_path))
    if first_valid_date is None:
        rng = _ticker_range(store, ticker)
        first_valid_date = rng["first"] if rng else None
    if not first_valid_date:
        return False
    series = store.get_stock_prices(ticker, str(first_valid_date)[:10], "9999-12-31")
    return len(series) >= int(min_days)


