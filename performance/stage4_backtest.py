"""Stage 4E-2: Walk-forward backtest simulation engine (pure, callable).

A deterministic, zero-look-ahead simulation driven entirely by the local price
cache built in Stage 4E-1 (:mod:`performance.backtest_data`). The engine reuses
the existing signal rules from :mod:`performance.stage4_signals` (50/200-DMA,
annualised volatility, AER, XIRR, dynamic GTT stop, the four sell signals and
the bottom-up conviction SIP ladder) rather than re-deriving any of them.

Public entry point
------------------
``run_backtest(config, market_db_path=..., txs=..., holdings=...)`` returns a
JSON-serialisable dict: ``daily_curve``, ``monthly_curve``, ``scorecard``,
``calendar_year_returns``, ``attribution`` and ``benchmarks``. The Streamlit UI
calls it directly under ``@st.cache_data`` (Option C — no HTTP server needed).

Real-world modelling: 0.25% friction on every buy/sell, Indian FIFO taxes (12.5%
LTCG above the Rs 1,25,000 annual exemption, 20% STCG), 0.0% on idle cash
(uninvested cash sits in the broker ledger and earns no yield),
a permanent lockout after an index-relative HARD_STOP_LOSS (fires only when below
200-DMA AND peak drawdown exceeds Nifty 500's by >=25pp; never re-entered),
and a 90-day cooling-off after a LAGGARD_EXIT that can be bypassed early on a confirmed
trend reclaim (CMP > 200-DMA AND 50-DMA > 200-DMA "golden cross"). A LAGGARD_EXIT
itself is refined here (engine-only) to fire only for positions held >= 730
calendar days that are below their 200-DMA AND lagging Nifty 500 by >= 15pp over
the holding period, so normal 12-month consolidations never liquidate compounders.
GTT_STOP_LOSS is likewise index-relative (fires only when the stock's drawdown
from peak exceeds Nifty 500's by >= 20pp), so broad market pullbacks do not stop
out healthy names; it is now evaluated monthly-only on the review day, selling
at that day's market price. Monthly fresh capital equals
max(0, real-ledger net buy inflow); index benchmarks (^NSEI / ^CRSLDX) accrue a
+1.30% p.a. dividend yield compounded daily so they reflect Total Return (TRI).
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from performance import backtest_data as bd
from performance.stage4_signals import (
    CONCENTRATION_TRIM,
    DMA_LONG,
    GTT_STOP_LOSS,
    HARD_STOP_LOSS,
    LAGGARD_EXIT,
    TRIM_AER,
    TRIM_TARGET_MULT,
    PortfolioTotals,
    StockInput,
    compute_bottom_up_sip_allocation,
    evaluate_signals,
)
from src.xirr import compute_xirr

# --------------------------------------------------------------------------- #
# Real-world friction / tax / cost-of-cash constants
# --------------------------------------------------------------------------- #
FRICTION_RATE = 0.0025          # 0.25% transaction/STT/slippage on every trade
STCG_RATE = 0.20                # 20% short-term capital gains tax
LTCG_RATE = 0.125              # 12.5% long-term capital gains tax
LTCG_EXEMPTION = 125000.0      # Rs 1,25,000 annual LTCG exemption
CASH_RATE_PA = 0.0           # 0.0% on uninvested cash (plain cash / broker ledger)
COOLDOWN_DAYS = 90             # cooling-off after a LAGGARD_EXIT (trend-reclaim can bypass)
LAGGARD_EXIT_MIN_HELD_DAYS = 730   # refined LAGGARD_EXIT: >= 2 full calendar years held
LAGGARD_EXIT_MIN_NIFTY_LAG = 0.15  # >= 15pp underperformance vs Nifty 500 over holding period
GTT_STOP_LOSS_MIN_NIFTY_LAG = 0.20  # GTT_STOP_LOSS fires only when stock drawdown
                                    # exceeds Nifty 500 drawdown by >= 20pp
CRASH_FREEZE_NIFTY_DD_THRESHOLD = -0.20  # Nifty 500 trailing-52w-high drawdown < -20% ->
                                    # crash freeze (pause GTT_STOP_LOSS / LAGGARD_EXIT sells)
LTCG_HOLD_DAYS = 365           # holding period that qualifies as long-term
DAYS_PER_YEAR = 365.0
MIN_HISTORY_DAYS = DMA_LONG    # 200 trading days of history before activation
INDEX_DIVIDEND_YIELD = 0.013   # +1.30% p.a. TRI dividend yield for ^NSEI / ^CRSLDX
WATCHLIST_MAX_YEARS = 3        # watchlist stocks may only activate in the last 3 years

VALID_YEARS = (3, 4, 5, 6, 7)

BACKTEST_CACHE_VERSION = 1
BACKTEST_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "backtest_cache"


def backtest_cache_dir(cache_dir: str | Path | None = None) -> Path:
    """Return the persistent backtest cache directory."""
    return Path(cache_dir) if cache_dir is not None else BACKTEST_CACHE_DIR


def backtest_cache_key(
    person_id: int | str | None = None,
    years: int = 7,
    compare_watchlist: bool = False,
    include_closed: bool = True,
    *,
    horizon_years: int | None = None,
    enable_quarantine_reentry: bool = False,
) -> str:
    """Build a deterministic cache key from run settings."""
    effective_years = int(horizon_years if horizon_years is not None else years)
    payload = json.dumps(
        {
            "person_id": str(person_id or ""),
            "horizon_years": effective_years,
            "compare_watchlist": bool(compare_watchlist),
            "include_closed": bool(include_closed),
            "enable_quarantine_reentry": bool(enable_quarantine_reentry),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_backtest_data_signature(
    market_db_path: str | None = None, txs: list[dict] | None = None
) -> str:
    """Hash the latest cached price date and transaction-ledger state."""
    db = bd._default_db(market_db_path)
    latest_price_date = None
    try:
        with sqlite3.connect(db) as conn:
            row = conn.execute("SELECT MAX(trade_date) FROM stock_prices").fetchone()
            latest_price_date = row[0] if row else None
    except Exception:  # noqa: BLE001
        latest_price_date = None

    latest_trade_date = None
    for tx in txs or []:
        raw_date = tx.get("trade_date") or tx.get("date") or tx.get("settlement_date")
        if raw_date:
            trade_date = str(raw_date)[:10]
            latest_trade_date = trade_date if not latest_trade_date else max(latest_trade_date, trade_date)

    payload = json.dumps(
        {
            "latest_price_date": str(latest_price_date or ""),
            "transaction_count": len(txs or []),
            "latest_trade_date": str(latest_trade_date or ""),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save_backtest_result(
    result: dict,
    *,
    cache_dir: str | Path | None = None,
    person_id: int | str | None = None,
    years: int = 7,
    compare_watchlist: bool = False,
    include_closed: bool = True,
    data_signature: str | None = None,
    saved_at: str | None = None,
    enable_quarantine_reentry: bool = False,
) -> Path:
    """Persist a backtest result together with lightweight run metadata."""
    directory = backtest_cache_dir(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    key = backtest_cache_key(
        person_id,
        years=years,
        compare_watchlist=compare_watchlist,
        include_closed=include_closed,
        enable_quarantine_reentry=enable_quarantine_reentry,
    )
    metadata = {
        "version": BACKTEST_CACHE_VERSION,
        "key": key,
        "person_id": str(person_id or ""),
        "years": int(years),
        "compare_watchlist": bool(compare_watchlist),
        "include_closed": bool(include_closed),
        "enable_quarantine_reentry": bool(enable_quarantine_reentry),
        "data_signature": data_signature or compute_backtest_data_signature(),
        "saved_at": saved_at or datetime.now().astimezone().isoformat(),
    }
    payload = {"version": BACKTEST_CACHE_VERSION, "metadata": metadata, "result": result}
    path = directory / f"backtest_{key}.json"
    path.write_text(
        json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def load_backtest_cache(
    cache_dir: str | Path | None = None,
    *,
    person_id: int | str | None = None,
    years: int = 7,
    compare_watchlist: bool = False,
    include_closed: bool = True,
    enable_quarantine_reentry: bool = False,
    data_signature: str | None = None,
) -> tuple[dict | None, dict | None]:
    """Load one saved backtest result and its metadata, if present and valid."""
    directory = backtest_cache_dir(cache_dir)
    key = backtest_cache_key(
        person_id,
        years=years,
        compare_watchlist=compare_watchlist,
        include_closed=include_closed,
        enable_quarantine_reentry=enable_quarantine_reentry,
    )
    path = directory / f"backtest_{key}.json"
    if not path.is_file():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None, None
        metadata = payload.get("metadata")
        result = payload.get("result")
        if not isinstance(metadata, dict) or not isinstance(result, dict):
            return None, None
        if metadata.get("version") != BACKTEST_CACHE_VERSION or metadata.get("key") != key:
            return None, None
        if data_signature is not None and metadata.get("data_signature") != data_signature:
            return None, None
        return result, metadata
    except (OSError, TypeError, ValueError):
        return None, None


def load_backtest_result(
    cache_dir: str | Path | None = None,
    *,
    person_id: int | str | None = None,
    years: int = 7,
    compare_watchlist: bool = False,
    include_closed: bool = True,
    data_signature: str | None = None,
    enable_quarantine_reentry: bool = False,
) -> dict | None:
    """Load a backtest result after validating cache schema and data freshness."""
    result, metadata = load_backtest_cache(
        cache_dir,
        person_id=person_id,
        years=years,
        compare_watchlist=compare_watchlist,
        include_closed=include_closed,
        enable_quarantine_reentry=enable_quarantine_reentry,
        data_signature=data_signature,
    )
    if result is None or metadata is None:
        return None
    return result


# Diagnostic-only sell-reason ordering for the Stage 4E audit table.
# HARD_STOP_LOSS / LAGGARD_EXIT / CONCENTRATION_TRIM are the strategy rules;
# GTT_STOP_LOSS is the price-based dynamic stop (now evaluated monthly-only on
# the review day). CATASTROPHE_GTT is retained only so the audit table can show
# it as an explicit zero row (the
# blind 45%-below-peak floor is disabled in this engine). Order is fixed so the
# rendered rows always print in a stable order and the TOTAL reconciles.
SELL_REASON_ORDER = (
    HARD_STOP_LOSS,
    LAGGARD_EXIT,
    CONCENTRATION_TRIM,
    GTT_STOP_LOSS,
    "CATASTROPHE_GTT",
)


@dataclass
class BacktestConfig:
    """Request parameters for one walk-forward run."""

    years: int = 7
    include_watchlist: bool = True
    include_closed_positions: bool = True
    base_unit_sip: float = 10000.0
    max_monthly_ceiling: float = 200000.0
    trim_aer: float = TRIM_AER
    trim_target_mult: float = TRIM_TARGET_MULT
    market_db_path: str | None = None
    start_date: str | None = None  # optional explicit backtest start (ISO)
    enable_quarantine_reentry: bool = False  # Exit -> Quarantine -> Golden-Cross Re-Entry

    def __post_init__(self):
        try:
            self.years = int(self.years)
        except (TypeError, ValueError):
            self.years = 7
        if self.years not in VALID_YEARS:
            self.years = 7
        try:
            self.enable_quarantine_reentry = bool(self.enable_quarantine_reentry)
        except (TypeError, ValueError):
            self.enable_quarantine_reentry = False


# --------------------------------------------------------------------------- #
# Small date / numeric helpers
# --------------------------------------------------------------------------- #
def _to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 Feb -> 28 Feb
        return d.replace(year=d.year - years, day=28)


def _financial_year(d: date) -> int:
    """Indian fiscal year ending label (Apr-Mar): Apr-2024 -> FY2025."""
    return d.year + 1 if d.month >= 4 else d.year


def _finite(x, default=None):
    """Return ``x`` as a float when finite, else ``default``."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _pct(x, default=None):
    f = _finite(x)
    return round(f * 100.0, 2) if f is not None else default


def _monthly_net_buy_inflow(txs) -> dict[tuple[int, int], float]:
    """Net real-ledger buy inflow per calendar month: ``{(year, month): net}``.

    ``net`` = Σ buy gross_amount − Σ sell gross_amount for that month. Callers
    floor it at zero so months with net real-life sales never inject negative
    cashflows (or force artificial benchmark sells).
    """
    monthly: dict[tuple[int, int], float] = {}
    for t in txs or []:
        side = str(t.get("buy_sell") or t.get("side") or "").strip().upper()
        if side not in ("BUY", "SELL"):
            continue
        raw_date = t.get("trade_date") or t.get("date") or t.get("settlement_date")
        if not raw_date:
            continue
        try:
            d = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        amt = _finite(t.get("gross_amount") or t.get("net_amount"), 0.0) or 0.0
        if not amt:
            qty = _finite(t.get("quantity"), 0.0) or 0.0
            rate = _finite(t.get("gross_rate") or t.get("price") or t.get("avg_price"), 0.0) or 0.0
            amt = qty * rate
        key = (d.year, d.month)
        if side == "BUY":
            monthly[key] = monthly.get(key, 0.0) + amt
        else:
            monthly[key] = monthly.get(key, 0.0) - amt
    return monthly


def _month_key_offset(d: date, months: int) -> tuple[int, int]:
    """Map a date to its calendar-month key N months ahead."""
    m = d.month - 1 + months
    return (d.year + m // 12, m % 12 + 1)


def _book_proceeds(net: float, d: date, rd) -> float:
    """Return net now but lock 2/3 for 3-month staggered redeployment."""
    net = _finite(net, 0.0) or 0.0
    if net <= 0 or rd is None:
        return net
    third = net / 3.0
    rd["pending"] = rd.get("pending", 0.0) + 2.0 * third
    rel = rd.setdefault("release", {})
    k1 = _month_key_offset(d, 1)
    k2 = _month_key_offset(d, 2)
    rel[k1] = rel.get(k1, 0.0) + third
    rel[k2] = rel.get(k2, 0.0) + third
    return net


# --------------------------------------------------------------------------- #
# Local-DB price index (no network, zero look-ahead)
# --------------------------------------------------------------------------- #
class PriceIndex:
    """Ascending ``(date, price)`` series per ticker, read from the local DB.

    Prices may be cached under the ``.NS`` ticker OR its ISIN (the price cache
    keys most series by ISIN). Every ticker therefore resolves across all its
    aliases — ``TICKER.NS``, bare ``TICKER`` and its ISIN — and merges the
    series so an active/closed/watchlist stock is never silently missed just
    because its rows live under a different key.
    """

    def __init__(self, tickers, market_db_path=None, isin_by_ticker=None):
        self.db = bd._default_db(market_db_path)
        self._series: dict[str, list[tuple[date, float]]] = {}
        isin_map = isin_by_ticker or {}
        for t in tickers:
            merged: dict[str, float] = {}
            for alias in self._aliases_for(t, isin_map.get(t)):
                for d, p in bd.cached_price_series(alias, market_db_path=self.db):
                    merged.setdefault(str(d)[:10], p)
            clean = []
            for dstr, p in merged.items():
                dd = _to_date(dstr)
                pf = _finite(p)
                if dd is not None and pf is not None and pf > 0:
                    clean.append((dd, pf))
            clean.sort(key=lambda x: x[0])
            self._series[t] = clean

    @staticmethod
    def _aliases_for(ticker, isin=None) -> list[str]:
        """Every cache key a stock's prices may legitimately live under."""
        t = str(ticker or "").strip().upper()
        out: list[str] = []
        for cand in (t, t[:-3] if t.endswith(".NS") else None,
                     str(isin).strip().upper() if isin else None):
            if cand and cand not in out:
                out.append(cand)
        return out

    def has(self, ticker) -> bool:
        return bool(self._series.get(ticker))

    def series(self, ticker) -> list[tuple[date, float]]:
        return self._series.get(ticker, [])

    def dates(self, ticker) -> list[date]:
        return [d for d, _ in self._series.get(ticker, [])]

    def all_dates(self, tickers=None) -> list[date]:
        keys = tickers if tickers is not None else self._series.keys()
        s: set[date] = set()
        for t in keys:
            s.update(self.dates(t))
        return sorted(s)

    def price_on(self, ticker, d: date) -> float | None:
        """Latest price at or before ``d`` (``None`` before the first print)."""
        best = None
        for dd, p in self._series.get(ticker, []):
            if dd <= d:
                best = p
            else:
                break
        return best

    def series_upto(self, ticker, d: date) -> list[float]:
        return [p for dd, p in self._series.get(ticker, []) if dd <= d]

    def dates_upto(self, ticker, d: date) -> list[date]:
        return [dd for dd, _ in self._series.get(ticker, []) if dd <= d]

    def first_ready_date(self, ticker, not_before: date,
                         min_days: int = MIN_HISTORY_DAYS) -> date | None:
        """First date >= ``not_before`` with ``min_days`` prior prints (200-DMA)."""
        for i, (dd, _p) in enumerate(self._series.get(ticker, [])):
            if dd >= not_before and (i + 1) >= min_days:
                return dd
        return None


@dataclass
class _Lot:
    qty: float
    price: float  # gross per-share trade price (friction tracked separately)
    date: date


@dataclass
class _StockState:
    ticker: str
    isin: str | None
    first_buy_date: date | None
    sources: set = field(default_factory=set)
    lots: list = field(default_factory=list)
    cooldown_until: date | None = None
    frozen: bool = False
    permanent_exited: bool = False  # HARD_STOP_LOSS -> never re-enter for the rest of the run
    activation_date: date | None = None
    gtt: float | None = None
    peak_price: float | None = None  # running high-water close (drawdown / 45% floor)
    invested: float = 0.0        # gross rupees deployed (buy cost before friction)
    proceeds: float = 0.0        # net rupees returned (after friction & tax)
    gross_proceeds: float = 0.0  # gross rupees from sells/trims (before friction & tax)
    trades: int = 0
    sip_multiplier: float = 0.0  # last evaluated SIP multiplier (0.0 = not eligible)
    strike_count: int = 0        # stop-loss exit strikes: 1 -> QUARANTINE, 2 -> DROPPED_TWO_STRIKES
    quarantine: bool = False     # Strike-1: stopped out, awaiting golden-cross re-entry
    dropped_two_strikes: bool = False  # Strike-2: permanently blocked after second stop

    @property
    def qty(self) -> float:
        return sum(lot.qty for lot in self.lots)

    @property
    def cost(self) -> float:
        return sum(lot.qty * lot.price for lot in self.lots)

    @property
    def avg(self) -> float:
        q = self.qty
        return (self.cost / q) if q > 1e-9 else 0.0

    @property
    def start(self) -> date | None:
        return self.lots[0].date if self.lots else None


# --------------------------------------------------------------------------- #
# Execution: friction, FIFO capital-gains tax
# --------------------------------------------------------------------------- #
def _execute_buy(st: _StockState, d: date, amount: float, price: float) -> float:
    """Buy ``amount`` of rupees worth (friction deducted). Returns friction paid."""
    amount = _finite(amount, 0.0) or 0.0
    price = _finite(price)
    if amount <= 0 or price is None or price <= 0:
        return 0.0
    friction = amount * FRICTION_RATE
    shares_value = amount - friction
    qty = shares_value / price
    if qty <= 1e-12:
        return 0.0
    st.lots.append(_Lot(qty=qty, price=price, date=d))
    st.invested += amount
    st.trades += 1
    return friction


def _capital_gains_tax(st: _StockState, d: date, price: float, qty: float,
                       tax_state: dict) -> tuple[float, float, float]:
    """FIFO-consume ``qty`` lots; return ``(ltcg_gain, stcg_gain, tax_due)``."""
    remaining = qty
    ltcg = 0.0
    stcg = 0.0
    while remaining > 1e-9 and st.lots:
        lot = st.lots[0]
        take = min(lot.qty, remaining)
        gain = (price - lot.price) * take
        held = (d - lot.date).days
        if held > LTCG_HOLD_DAYS:
            ltcg += gain
        else:
            stcg += gain
        lot.qty -= take
        remaining -= take
        if lot.qty <= 1e-9:
            st.lots.pop(0)
    tax = 0.0
    if stcg > 0:
        tax += stcg * STCG_RATE
    if ltcg > 0:
        fy = _financial_year(d)
        cum = tax_state["ltcg_by_fy"].get(fy, 0.0)
        before = max(0.0, cum - LTCG_EXEMPTION)
        after = max(0.0, cum + ltcg - LTCG_EXEMPTION)
        tax += (after - before) * LTCG_RATE
        tax_state["ltcg_by_fy"][fy] = cum + ltcg
    return ltcg, stcg, tax


def _execute_sell(st: _StockState, d: date, price: float, fraction: float,
                  tax_state: dict) -> tuple[float, float, float]:
    """Sell ``fraction`` of the position. Returns ``(net_cash, friction, tax)``."""
    fraction = _finite(fraction, 0.0) or 0.0
    price = _finite(price)
    if fraction <= 0 or price is None or price <= 0:
        return 0.0, 0.0, 0.0
    qty = min(st.qty, st.qty * min(1.0, fraction))
    if qty <= 1e-12:
        return 0.0, 0.0, 0.0
    gross = qty * price
    friction = gross * FRICTION_RATE
    _ltcg, _stcg, tax = _capital_gains_tax(st, d, price, qty, tax_state)
    net = gross - friction - tax
    st.proceeds += net
    st.gross_proceeds += gross
    st.trades += 1
    return net, friction, tax


# --------------------------------------------------------------------------- #
# Universe classification (active / watchlist / closed) & benchmark resolution
# --------------------------------------------------------------------------- #
def _classify_universe(tickers, txs, holdings, db) -> dict[str, set]:
    """Map each universe ticker to the source(s) it came from."""
    from market_data.prices import normalize_security_id

    sources: dict[str, set] = {t: set() for t in tickers}

    def _ticker_for(ident_value):
        if not ident_value:
            return None
        ident = normalize_security_id(ident_value, db_path=db)
        tk = ident.get("nse_ticker")
        # Guarantee the same .NS-normalised key load_backtest_universe() stores so
        # active/closed/watchlist stocks always resolve to their universe entry.
        return bd._norm_ticker(tk) if tk else None

    if holdings is not None and hasattr(holdings, "iterrows"):
        for _, r in holdings.iterrows():
            try:
                qty = float(r.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if qty <= 1e-6:
                continue
            tk = _ticker_for(r.get("isin"))
            if tk not in sources:
                tk = _ticker_for(r.get("symbol"))  # ISIN unmapped -> resolve by symbol
            if tk in sources:
                sources[tk].add("active")

    try:
        from market_data.repository import MarketDataStore

        store = MarketDataStore(db)
        for row in store.get_watchlist_stocks():
            tk = _ticker_for(row.get("nse_ticker") or row.get("isin"))
            if tk in sources:
                sources[tk].add("watchlist")
    except Exception:  # noqa: BLE001, S110
        pass

    for isin in bd._net_qty_closed_isins(txs or []):
        tk = _ticker_for(isin)
        if tk in sources:
            sources[tk].add("closed")

    return sources


def _resolve_benchmark(prices: PriceIndex, candidates) -> str | None:
    for t in candidates:
        if prices.has(t):
            return t
    return None


def _nifty500_price(prices: PriceIndex, d: date) -> float | None:
    """Nifty 500 close at-or-before ``d`` (^CRSLDX, MONIFTY500.NS ETF fallback).

    Falls back to ``MONIFTY500.NS`` whenever ``^CRSLDX`` has no print on/before
    the requested date, so the LAGGARD_EXIT lag test is never skipped merely
    because one index series has a gap.
    """
    for ticker in bd.NIFTY500_TICKERS:
        px = prices.price_on(ticker, d)
        if px is not None and px > 0:
            return px
    return None


def _nifty500_52w_drawdown(prices: PriceIndex, d: date) -> float | None:
    """Trailing 52-week-high drawdown of the Nifty 500 (^CRSLDX / MONIFTY500.NS).

    Drawdown is expressed as a NEGATIVE fraction of the trailing 52-week high
    (``(end_px - high_px) / high_px``), i.e. -0.20 == 20% peak-to-trough crash.
    Returns ``None`` when the benchmark price is not measurable at-or-before
    ``d`` (unavailable -> no freeze).
    """
    end_px = _nifty500_price(prices, d)
    if end_px is None or end_px <= 0:
        return None
    window_start = d - timedelta(days=365)  # ~52 weeks of trading history
    high: float | None = None
    for ticker in bd.NIFTY500_TICKERS:
        px_series = prices.series_upto(ticker, d)
        dd_series = prices.dates_upto(ticker, d)
        for dd, px in zip(dd_series, px_series):
            if dd < window_start or px is None or px <= 0:
                continue
            if high is None or px > high:
                high = px
    if high is None or high <= 0:
        return None
    return (end_px - high) / high


def _benchmark_daily_curve(prices: PriceIndex, tickers: list[str], flows,
                           window_dates: list[date], first_buy_map=None,
                           dividend_yield: float = 0.0) -> tuple[list[tuple[date, float]], dict]:
    """Reconstruct a cash-flow-matched benchmark's daily equity curve.

    Each SIP flow (a negative rupee amount) is split equally across the tickers
    that print a price on that date AND are already active (``first_buy_map``
    gates discovery so a benchmark never back-dates a not-yet-owned stock). Units
    accumulate and are marked to market on every trading day in ``window_dates``
    at the latest available close (forward-filled). When ``dividend_yield`` > 0
    (an index TRI proxy), each lot accrues a daily-compounded yield from its
    acquisition date so the curve reflects Total Return rather than price-only.
    Returns ``(daily_curve, units_by_ticker)``.
    """
    units: dict[str, float] = {t: 0.0 for t in tickers}
    lots: dict[str, list[tuple[float, date]]] = {t: [] for t in tickers}
    daily_yield = (dividend_yield / DAYS_PER_YEAR) if dividend_yield else 0.0
    flow_map: dict[date, float] = {}
    for d, amt in flows:
        amt = _finite(amt, 0.0) or 0.0
        flow_map[d] = flow_map.get(d, 0.0) + amt  # negative = money in
    daily: list[tuple[date, float]] = []
    for d in window_dates:
        amt = flow_map.get(d)
        if amt is not None and amt < 0 and tickers:
            eligible = []
            for t in tickers:
                if (prices.price_on(t, d) or 0.0) <= 0:
                    continue
                if first_buy_map is not None:
                    fbd = first_buy_map.get(t)
                    if fbd is not None and fbd > d:
                        continue  # not yet discovered / owned on this date
                eligible.append(t)
            if eligible:
                per = (-amt) / len(eligible)
                for t in eligible:
                    lvl = prices.price_on(t, d)
                    units[t] += per / lvl
                    lots[t].append((per / lvl, d))
        if daily_yield > 0:
            value = 0.0
            for t in tickers:
                px = prices.price_on(t, d)
                if not px or px <= 0:
                    continue
                for u, acq in lots[t]:
                    days = (d - acq).days
                    value += u * px * ((1.0 + daily_yield) ** days)
        else:
            value = sum(u * (prices.price_on(t, d) or 0.0) for t, u in units.items())
        daily.append((d, _finite(value, 0.0) or 0.0))
    return daily, units


def _monthly_points_from_daily(daily) -> list[dict]:
    """Collapse a daily ``[(date, value)]`` curve to one point per calendar month."""
    out: list[dict] = []
    for d, v in daily:
        if out and out[-1]["_ym"] == (d.year, d.month):
            out[-1].update({"date": d.isoformat(), "value": round(v, 2)})
        else:
            out.append({"_ym": (d.year, d.month), "date": d.isoformat(),
                        "value": round(v, 2)})
    for m in out:
        m.pop("_ym", None)
    return out


def _benchmark_metrics(prices: PriceIndex, tickers, flows, window_dates,
                       contributions, year_trading_days, start: date, end: date,
                       method: str, first_buy_map=None, dividend_yield: float = 0.0) -> dict:
    """Full risk/return metrics for a benchmark from its reconstructed curve.

    XIRR uses the exact SIP cashflows (``-amount`` on each contribution date)
    plus the positive terminal value on the end date; Max Drawdown, the
    Return / Drawdown ratio and Calendar-Year Returns are derived from the daily
    equity curve exactly as for the simulated strategy, so they are never None.
    """
    invested = sum(-(_finite(a, 0.0) or 0.0) for _d, a in flows)
    daily, units = _benchmark_daily_curve(prices, list(tickers), flows, window_dates,
                                         first_buy_map=first_buy_map,
                                         dividend_yield=dividend_yield)
    final_value = daily[-1][1] if daily else 0.0
    mdd = _max_drawdown([v for _d, v in daily])
    total_return = (final_value / invested - 1.0) if invested > 0 else None
    if mdd > 0:
        rd_ratio = (total_return / mdd) if total_return is not None else None
    elif total_return is not None and total_return > 0:
        rd_ratio = total_return  # zero drawdown -> ratio collapses to raw return
    else:
        rd_ratio = 0.0 if total_return is not None else None
    rd_ratio = round(rd_ratio, 4) if rd_ratio is not None else None
    term_date = daily[-1][0] if daily else end
    cfs = [(d, _finite(a, 0.0) or 0.0) for d, a in flows] + \
          [(term_date, _finite(final_value, 0.0) or 0.0)]
    xirr = compute_xirr(cfs)
    monthly = _monthly_points_from_daily(daily)
    return {
        "invested": round(invested, 2),
        "final_value": round(_finite(final_value, 0.0) or 0.0, 2),
        "xirr": xirr,
        "post_tax_xirr_pct": _pct(xirr),
        "pre_tax_xirr_pct": _pct(xirr),  # buy & hold benchmark pays no tax
        "max_drawdown_pct": _pct(mdd),
        "return_drawdown_ratio": rd_ratio,
        "calendar_year_returns": _calendar_year_returns(monthly, contributions,
                                                        year_trading_days),
        "daily_curve": [{"date": d.isoformat(), "value": round(v, 2)}
                        for d, v in daily],
        "monthly_curve": monthly,
        "units": units,
        "tickers": list(tickers),
        "method": method,
    }


def _sim_index_benchmark(prices: PriceIndex, ticker: str | None, flows,
                         window_dates, contributions, year_trading_days,
                         start: date, end: date,
                         dividend_yield: float = 0.0) -> dict:
    """Cash-flow matched index benchmark (same rupees, same dates, optional TRI)."""
    tickers = [ticker] if ticker else []
    return _benchmark_metrics(prices, tickers, flows, window_dates, contributions,
                              year_trading_days, start, end, method="index",
                              dividend_yield=dividend_yield)


def _flow_out(d: date, amt: float) -> tuple[str, float]:
    return (d.isoformat(), round(_finite(amt, 0.0) or 0.0, 2))


def _record_sell_stats(stats: dict, reason: str, gross: float, friction: float,
                       tax: float) -> None:
    """Accumulate per-reason sell accounting into ``stats`` (diagnostic only)."""
    bucket = stats.setdefault(
        "sells", {}).setdefault(
        reason, {"count": 0, "gross": 0.0, "net": 0.0, "tax": 0.0, "friction": 0.0})
    bucket["count"] += 1
    bucket["gross"] += gross
    bucket["net"] += gross - friction - tax
    bucket["tax"] += tax
    bucket["friction"] += friction


def _apply_sell(st: _StockState, d: date, price: float, frac: float,
                tax_state: dict, stats: dict, reason: str = "") -> float:
    """Execute a sell and fold friction/tax into ``stats``; return net cash."""
    before_gross = st.gross_proceeds
    net, friction, tax = _execute_sell(st, d, price, frac, tax_state)
    stats["friction"] += friction
    stats["taxes"] += tax
    gross = st.gross_proceeds - before_gross
    if reason:
        _record_sell_stats(stats, reason, gross, friction, tax)
    return net


def _first_on_after(prices: PriceIndex, ticker: str, d: date) -> float | None:
    for dd, p in prices.series(ticker):
        if dd >= d:
            return p
    return None


def _benchmark_xirr(prices: PriceIndex, d: date, start: date) -> float | None:
    """Point-to-point annualised Nifty 50 return up to ``d`` (efficiency baseline)."""
    ticker = _resolve_benchmark(prices, [bd.NIFTY50_TICKER, bd.NIFTY50_FALLBACK])
    if ticker is None:
        return None
    a = _first_on_after(prices, ticker, start)
    b = prices.price_on(ticker, d)
    if a is None or b is None or a <= 0:
        return None
    years = (d - start).days / DAYS_PER_YEAR
    if years <= 0.02:
        return None
    try:
        return (b / a) ** (1.0 / years) - 1.0
    except (ValueError, ZeroDivisionError):
        return None


def _sim_equal_weight_benchmark(prices: PriceIndex, sim_tickers, flows,
                                window_dates, contributions, year_trading_days,
                                start: date, end: date, first_buy_map=None) -> dict:
    """Equal-weight buy & hold: split each flow equally across the stocks that
    were already active in the real portfolio on that date (zero stops / trims)."""
    return _benchmark_metrics(prices, list(sim_tickers), flows, window_dates,
                              contributions, year_trading_days, start, end,
                              method="equal_weight_buy_and_hold",
                              first_buy_map=first_buy_map)


# --------------------------------------------------------------------------- #
# Intra-month daily GTT stop-loss scan
# --------------------------------------------------------------------------- #
def _intraday_gtt(states, prices: PriceIndex, d: date, cash: float,
                  tax_state: dict, stats: dict, rd=None) -> float:
    for st in states:
        if st.activation_date is None or st.activation_date > d:
            continue
        if st.qty <= 1e-9 or st.frozen or st.permanent_exited:
            continue
        px = prices.price_on(st.ticker, d)
        if px is None:
            continue
        # Track the running high-water close. Still required by the
        # index-relative HARD_STOP_LOSS drawdown math below; the blind 45%
        # catastrophe floor that previously auto-liquidated here (and set the
        # permanent ban) has been removed.
        st.peak_price = max(st.peak_price or px, px)
        # NOTE (Stage 4E monthly-only GTT refinement): the signal-based dynamic
        # GTT_STOP_LOSS is now evaluated ONLY on monthly review days inside
        # _monthly_step, where it sells at the current review-day market price
        # (cmp) rather than the historical intraday trigger level. This daily tick
        # therefore keeps just the running high-water peak tracking, which the
        # index-relative HARD_STOP_LOSS drawdown math still relies on.
    return cash


# --------------------------------------------------------------------------- #
# Monthly check-in: signal evaluation, exits/trims and bottom-up SIPs
# --------------------------------------------------------------------------- #
def _build_stock_input(st: _StockState, prices: PriceIndex, d: date) -> StockInput | None:
    series = prices.series_upto(st.ticker, d)
    if len(series) < MIN_HISTORY_DAYS:
        return None
    cmp = series[-1]
    xirr = None
    if st.qty > 1e-9:
        cf = [(lot.date, -lot.qty * lot.price) for lot in st.lots]
        cf.append((d, st.qty * cmp))
        xirr = compute_xirr(cf)
    return StockInput(
        security_id=st.ticker, symbol=st.ticker, quantity=st.qty,
        avg_buy_price=st.avg, invested_cost=st.cost, market_value=st.qty * cmp,
        xirr=xirr, holding_start_date=st.start,
        price_series=series, price_dates=prices.dates_upto(st.ticker, d),
    )


def _laggard_exit_allowed(st: _StockState, row: dict, cmp: float,
                          prices: PriceIndex, d: date) -> bool:
    """Stage 4E refinement: exit only true 2-year dead-weight laggards.

    Requires ALL of:
      1. held >= 730 calendar days (2 full years), and
      2. trading below the 200-DMA, and
      3. >= 15pp underperformance vs Nifty 500 over the holding period.
    If the Nifty 500 lag cannot be measured (no index/price data), the position
    is held rather than blind-exited. Normal 12-month consolidations therefore
    never liquidate a good compounder.
    """
    held_days = (d - st.start).days if st.start is not None else 0
    dma200 = _finite(row.get("dma_200"))
    if held_days < LAGGARD_EXIT_MIN_HELD_DAYS or not (dma200 and cmp < dma200):
        return False
    nifty_start_px = _nifty500_price(prices, st.start) if st.start else None
    nifty_now_px = _nifty500_price(prices, d)
    first_px = st.lots[0].price if st.lots else st.avg
    if not (nifty_start_px and nifty_now_px and nifty_start_px > 0 and first_px > 0):
        return False
    stock_ret = cmp / first_px - 1.0
    nifty_ret = nifty_now_px / nifty_start_px - 1.0
    return (nifty_ret - stock_ret) >= LAGGARD_EXIT_MIN_NIFTY_LAG


def _gtt_stop_allowed(st: _StockState, px: float, prices: PriceIndex,
                      d: date, rd: dict | None = None) -> bool:
    """Index-relative GTT stop gate (Stage 4E).

    Fires only when the stock's drawdown from its own peak ALSO exceeds Nifty
    500's drawdown over the same peak-to-current window by >= 20pp:
    ``(stock_dd - nifty_dd) >= GTT_STOP_LOSS_MIN_NIFTY_LAG``. Broad market
    pullbacks (stock and Nifty 500 falling together) therefore do not trigger
    false stop-outs; genuine company-specific collapses still exit cleanly.
    If the Nifty 500 drawdown cannot be measured, the position is held rather
    than blind-stopped.
    """
    peak = st.peak_price or 0.0
    if peak <= 0:
        return False
    stock_dd = (peak - px) / peak
    nifty_peak = (rd.get("nifty500_peak", 0.0) if rd is not None else 0.0) or 0.0
    nifty_now = _nifty500_price(prices, d)
    if not (nifty_peak > 0 and nifty_now and nifty_now > 0):
        return False
    nifty_dd = (nifty_peak - nifty_now) / nifty_peak
    return (stock_dd - nifty_dd) >= GTT_STOP_LOSS_MIN_NIFTY_LAG


def _lifecycle_stats(stats: dict) -> dict:
    """Per-run Exit -> Quarantine -> Re-Entry -> Two-Strikes diagnostics."""
    lg = stats.setdefault("lifecycle", {
        "quarantined_count": 0,
        "reentered_count": 0,
        "dropped_two_strikes": 0,
    })
    lg.setdefault("quarantined_count", 0)
    lg.setdefault("reentered_count", 0)
    lg.setdefault("dropped_two_strikes", 0)
    lg.setdefault("dropped_tickers", [])
    return lg


def _apply_stop_loss_strike(st: _StockState, reason: str, d: date, enable_lifecycle: bool,
                            lg: dict) -> None:
    """Record a stop-loss exit strike against ``st`` (Exit -> Quarantine ...).

    With ``enable_lifecycle`` disabled the locked behaviour is preserved: a
    HARD_STOP_LOSS permanent lockout (never re-enter) and a GTT_STOP_LOSS
    cooldown (90 days). With the lifecycle enabled:

    - Strike 1 (first stop-loss exit): the ticker moves to ``QUARANTINE`` and
      fresh Stage 4C SIP allocations are blocked (frozen + cooling-off window).
      ``lifecycle["quarantined_count"]`` increments.
    - Strike 2 (second stop-loss exit, only reachable after a golden-cross
      re-entry cleared the quarantine): the ticker is marked
      ``DROPPED_TWO_STRIKES`` and permanently blocked from future SIPs/re-entry.
      ``lifecycle["dropped_two_strikes"]`` increments.
    """
    if not enable_lifecycle:
        if reason == HARD_STOP_LOSS:
            st.permanent_exited = True
            st.frozen = True
        else:
            st.cooldown_until = d + timedelta(days=COOLDOWN_DAYS)
            st.frozen = True
        return
    prev_quarantined = bool(st.quarantine)
    st.strike_count = int(getattr(st, "strike_count", 0) or 0) + 1
    if st.strike_count >= 2:
        st.dropped_two_strikes = True
        st.permanent_exited = True
        st.frozen = True
        st.cooldown_until = d + timedelta(days=COOLDOWN_DAYS)
        st.sip_multiplier = 0.0
        lg["dropped_two_strikes"] = int(lg.get("dropped_two_strikes") or 0) + 1
        _dropped = lg.get("dropped_tickers") or []
        if st.ticker not in _dropped:
            _dropped.append(st.ticker)
        lg["dropped_tickers"] = sorted(_dropped)
        return
    st.quarantine = True
    st.frozen = True
    st.cooldown_until = d + timedelta(days=COOLDOWN_DAYS)
    st.sip_multiplier = 0.0
    if not prev_quarantined:
        lg["quarantined_count"] = int(lg.get("quarantined_count") or 0) + 1


def _aer_stats(values) -> dict:
    """Diagnostic: min / median / max of observed AER values (pure reporting)."""
    vals = sorted(float(v) for v in (values or []) if v is not None)
    if not vals:
        return {"count": 0, "min": None, "median": None, "max": None}
    mid = len(vals) // 2
    median = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
    return {"count": len(vals), "min": round(vals[0], 4),
            "median": round(median, 4), "max": round(vals[-1], 4)}


def _note_cash_idle(stats: dict, cash: float, eligible_count: int,
                    budget: float, allocated: float) -> None:
    """Diagnostic only: classify months ending with > Rs 50,000 idle cash.

    Buckets: (a) no_eligible — zero stocks with sip_multiplier > 0 (all paused /
    in cooldown); (b) per_stock_caps — the allocator could not absorb the budget
    because of per-stock caps / granularity; (c) budget_limited — the budget
    itself (fresh inflow / max_monthly_ceiling) was smaller than idle cash.
    """
    if cash is None or cash <= 50000.0:
        return
    bucket = stats.setdefault("cash_idle", {
        "total": 0, "no_eligible": 0, "budget_limited": 0, "per_stock_caps": 0,
    })
    bucket["total"] += 1
    if eligible_count <= 0:
        bucket["no_eligible"] += 1
    elif budget > 0 and allocated < budget - 1.0:
        bucket["per_stock_caps"] += 1
    else:
        bucket["budget_limited"] += 1


def _monthly_step(states, prices: PriceIndex, d: date, cash: float,
                  config: BacktestConfig, tax_state: dict, benchmark_xirr: float | None,
                  contributions: list, stats: dict, monthly_inflow: float = 0.0, rd=None) -> float:
    if rd is not None:
        released = rd.get("release", {}).pop((d.year, d.month), 0.0)
        rd["pending"] = max(0.0, rd.get("pending", 0.0) - released)
    lg = _lifecycle_stats(stats)
    active = [st for st in states
              if st.activation_date is not None and st.activation_date <= d]
    if not active:
        _note_cash_idle(stats, cash, 0, 0.0, 0.0)
        return cash

    mv = {st.ticker: (prices.price_on(st.ticker, d) or 0.0) for st in active}
    totals = PortfolioTotals(
        total_invested_cost=sum(st.cost for st in active),
        total_market_value=sum(st.qty * mv[st.ticker] for st in active),
    )
    stocks = [si for si in (_build_stock_input(st, prices, d) for st in active) if si]
    if not stocks:
        _note_cash_idle(stats, cash, 0, 0.0, 0.0)
        return cash

    rows = evaluate_signals(stocks, totals, benchmark_xirr, as_of_date=d,
                            trim_aer=config.trim_aer,
                            trim_target_mult=config.trim_target_mult)
    by_ticker = {st.ticker: st for st in active}
    row_by_ticker = {r["security_id"]: r for r in rows}
    # Diagnostic: record every evaluated AER for the min/median/max report.
    _aer_vals = [float(r["aer"]) for r in rows if r.get("aer") is not None]
    if _aer_vals:
        stats.setdefault("aer_samples", []).extend(_aer_vals)

    # Propagate the evaluated SIP multiplier into every active state so the
    # year-end audit can count eligible buy stocks (sip_multiplier > 0) at each
    # trading day. Unactivated states keep the dataclass default of 0.0.
    for st in states:
        if st.activation_date is not None:
            _mult = row_by_ticker.get(st.ticker, {}).get("sip_multiplier", 0.0) or 0.0
            if st.quarantine or st.dropped_two_strikes:
                # QUARANTINE (Strike 1) / DROPPED_TWO_STRIKES (Strike 2): no fresh
                # Stage 4C SIP allocation until / unless the lifecycle clears them.
                _mult = 0.0
            st.sip_multiplier = _mult

    # ---- Market crash freeze (Exp #8 diagnostic): Nifty 500 trailing-52w-high ----
    # A drawdown < -20% from the trailing 52-week high marks a broad market crash;
    # for that monthly review month the defensive GTT_STOP_LOSS / LAGGARD_EXIT sells
    # are paused (crash freeze) so index-driven risk reduction does not add to
    # drawdown during a crash. Normal behaviour otherwise; no freeze when the
    # benchmark price is unavailable. DIAGNOSTIC ONLY for Exp #8 (no rule change
    # wired yet - strategy rules untouched).
    nifty_dd_now = _nifty500_52w_drawdown(prices, d)
    crash_freeze = nifty_dd_now is not None and nifty_dd_now < CRASH_FREEZE_NIFTY_DD_THRESHOLD
    cf = stats.setdefault("crash_freeze", {"months": [], "nifty_dd": None, "triggered": 0,
                                         "suppressed_gtt": 0, "suppressed_laggard": 0,
                                         "drawdowns": []})
    if crash_freeze:
        cf["months"].append(d.isoformat())
        cf["drawdowns"].append(nifty_dd_now)
        cf["nifty_dd"] = nifty_dd_now
        cf["triggered"] += 1

    # ---- Monthly exits & trims (priority order already applied in evaluate_signals) ----
    for row in rows:
        st = by_ticker.get(row["security_id"])
        if st is None:
            continue
        st.gtt = _finite(row.get("gtt_stop_loss_price"))
        cmp = _finite(row.get("cmp"))
        if cmp is None or cmp <= 0 or st.qty <= 1e-9:
            continue
        # Track the running high-water close used for drawdown math.
        st.peak_price = max(st.peak_price or cmp, cmp)
        sig = row.get("signal_type")
        if sig == HARD_STOP_LOSS:
            # Index-relative exit: fire only when the stock is below its 200-DMA
            # AND its drawdown from peak exceeds Nifty 500 (^CRSLDX) drawdown
            # over the same window by >=25pp - so broad market pullbacks do not
            # force mass exits into cash, while stock-specific collapses (Yes
            # Bank / PI Industries) still exit cleanly.
            dma200 = _finite(row.get("dma_200"))
            stock_dd = ((st.peak_price - cmp) / st.peak_price) if st.peak_price else 0.0
            nifty_px = prices.price_on("^CRSLDX", d)
            nifty_peak = (rd.get("nifty500_peak", 0.0) if rd is not None else 0.0) or 0.0
            if nifty_peak > 0 and nifty_px is not None and nifty_px > 0:
                nifty_dd = (nifty_peak - nifty_px) / nifty_peak
            else:
                nifty_dd = 0.0
            if not (dma200 and cmp < dma200) or (stock_dd - nifty_dd) < 0.25:
                continue  # not a stock-specific collapse below 200-DMA -> hold
            cash += _book_proceeds(_apply_sell(st, d, cmp, 1.0, tax_state, stats, HARD_STOP_LOSS), d, rd)
            # Exit -> Quarantine -> Golden-Cross Re-Entry -> Two-Strikes lifecycle
            # (optional; the locked default keeps the permanent lockout after a
            # HARD_STOP_LOSS so the historical behaviour is 100% preserved).
            if config.enable_quarantine_reentry:
                _apply_stop_loss_strike(st, HARD_STOP_LOSS, d, True, lg)
            else:
                # Permanent lockout: a broken story (Yes Bank / PI Industries) is
                # never re-entered for the rest of the simulation.
                st.permanent_exited = True
                st.frozen = True
        elif sig == CONCENTRATION_TRIM:
            shares = _finite(row.get("shares_to_sell"), 0.0) or 0.0
            if shares > 0:
                cash += _book_proceeds(_apply_sell(st, d, cmp, min(1.0, shares / st.qty), tax_state, stats, CONCENTRATION_TRIM), d, rd)
        elif sig == LAGGARD_EXIT:
            # Market crash freeze (Exp #8 diagnostic): during a >=20% Nifty 500
            # drawdown the defensive laggard exits pause for this review month.
            if crash_freeze:
                cf["suppressed_laggard"] = int(cf.get("suppressed_laggard") or 0) + 1
                continue
            # Refined rule: only exit when held >= 730 days, below the 200-DMA,
            # AND lagging Nifty 500 by >= 15pp over the holding period. Anything
            # younger (normal 12-month consolidation) is held.
            if not _laggard_exit_allowed(st, row, cmp, prices, d):
                continue
            cash += _book_proceeds(_apply_sell(st, d, cmp, 1.0, tax_state, stats, LAGGARD_EXIT), d, rd)
            st.cooldown_until = d + timedelta(days=COOLDOWN_DAYS)
            st.frozen = True

    # ---- Monthly GTT_STOP_LOSS (index-relative dynamic stop) ----
    # Permanent wiring of the validated Stage 4 Exp #6 Variant B (monthly-only
    # GTT_STOP_LOSS): evaluated only on monthly review days, here, where it sells
    # at the current review-day market price (cmp) rather than the historical
    # intraday trigger level. Runs after the hard-stop / trim / laggard exits and
    # BEFORE the bottom-up SIP buys below, so proceeds freed this month are
    # redeployed into in-month eligible SIPs by the same review.
    for row in rows:
        st = by_ticker.get(row["security_id"])
        if st is None or st.qty <= 1e-9 or st.frozen or st.permanent_exited:
            continue
        if st.gtt is None:
            continue
        cmp_gtt = _finite(row.get("cmp"))
        if cmp_gtt is None or cmp_gtt <= 0 or cmp_gtt > st.gtt:
            continue  # trailing stop not yet breached at the monthly review
        if crash_freeze:
            cf["suppressed_gtt"] = int(cf.get("suppressed_gtt") or 0) + 1
            continue  # market crash freeze (Nifty 52w DD < -20%) -> hold through crash
        if not _gtt_stop_allowed(st, cmp_gtt, prices, d, rd):
            continue  # broad-market pullback -> hold healthy names
        cash += _book_proceeds(_apply_sell(st, d, cmp_gtt, 1.0, tax_state, stats, GTT_STOP_LOSS), d, rd)
        if config.enable_quarantine_reentry:
            _apply_stop_loss_strike(st, GTT_STOP_LOSS, d, True, lg)
        else:
            st.cooldown_until = d + timedelta(days=COOLDOWN_DAYS)
            st.frozen = True

    # ---- Bottom-up conviction SIPs (Rs 3L is a ceiling, not a target) ----
    sip_rows = [{
        "security_id": r["security_id"],
        "sip_multiplier": r.get("sip_multiplier"),
        "cmp": r.get("cmp"),
        "incubation": r.get("incubation", False),
    } for r in rows]

    # Fresh capital this month = max(0, real-ledger net buy inflow); months with
    # net real-life sales inject nothing (never a negative cashflow / artificial
    # benchmark sell). This is what XIRR/benchmarks treat as the external cashflow.
    monthly_contribution = max(0.0, _finite(monthly_inflow, 0.0) or 0.0)
    cash += monthly_contribution
    if monthly_contribution > 0:
        contributions.append((d, monthly_contribution))

    # Deployment budget = the whole available liquid pool (this month's fresh
    # capital PLUS recycled trim/exit proceeds), capped only by friction (cash / (1+FRICTION_RATE)).
    # This deploys up to 100% of the pool across eligible stocks instead of
    # stranding ~95% in cash; the pool is held in the Liquid Reserve only when
    # every stock is paused (0.0x run-up / broken trend).
    deployable = cash - (rd.get("pending", 0.0) if rd is not None else 0.0)
    budget = deployable / (1.0 + FRICTION_RATE) if deployable > 0 else 0.0

    # Dynamically scale the Base Unit SIP so healthy Lane 1 (2.5x) / Lane 2 (1.0x)
    # names collectively consume ~100% of the budget — respecting the 2.5x vs 1.0x
    # weights and a 25% single-stock concentration cap. The allocator's downscale +
    # leftover-redistribution path fills each stock up to the cap.
    eligible_mults = [float(r.get("sip_multiplier") or 0.0) for r in sip_rows
                      if float(r.get("sip_multiplier") or 0.0) > 0]
    max_single_cap = budget
    scaled_base = config.base_unit_sip
    if budget > 0 and eligible_mults:
        weight_sum = sum(eligible_mults)
        # Scale base_unit_sip so qualifying stocks absorb up to the full
        # monthly budget (min(deployable, max_monthly_ceiling)) instead of
        # leaving it stranded at base_unit_sip (e.g. ₹10k) when few stocks
        # are eligible. effective_base_unit = max(base_unit_sip, budget / total_mult).
        scaled_base = max(config.base_unit_sip, budget / weight_sum)
    alloc = compute_bottom_up_sip_allocation(
        sip_rows, base_unit_sip=scaled_base,
        max_budget_ceiling=budget,
        max_single_stock_sip=max_single_cap if budget > 0 else None)

    # Undeployed cash stays parked as plain cash (0.0% yield); every buy deducts from it.
    for ar in alloc.get("rows", []):
        st = by_ticker.get(ar["security_id"])
        if st is None:
            continue
        amt = _finite(ar.get("recommended_sip_rupees"), 0.0) or 0.0
        if amt <= 0:
            continue
        px = prices.price_on(st.ticker, d)
        if px is None or px <= 0:
            continue
        dma200 = _finite(row_by_ticker.get(st.ticker, {}).get("dma_200"))
        dma50 = _finite(row_by_ticker.get(st.ticker, {}).get("dma_50"))
        if st.permanent_exited:
            continue  # permanent lockout after HARD_STOP_LOSS — never re-enter
        # Confirmed golden-cross uptrend reclaim: CMP > 200-DMA AND 50-DMA > 200-DMA.
        golden_cross = bool(dma200 and dma50 and px > dma200 and dma50 > dma200)
        if st.quarantine and st.cooldown_until is not None and d > st.cooldown_until and golden_cross:
            # Mechanical re-entry (Exit -> Quarantine -> Golden-Cross Re-Entry):
            # a QUARANTINEd ticker becomes SIP-eligible again on a monthly review
            # date only once the trend is reclaimed (Close > DMA200 AND
            # DMA50 > DMA200).
            lg["reentered_count"] = int(lg.get("reentered_count") or 0) + 1
            st.quarantine = False
            # Strike counter is intentionally NOT reset here: after a golden-cross
            # re-entry the position is live again but still holds "Strike 1" armed,
            # so the next stop-loss exit must escalate to Strike 2 (drop / permanent
            # block) rather than restarting the quarantine ladder from Strike 1.
            st.strike_count = int(getattr(st, "strike_count", 0) or 0) or 1
            st.cooldown_until = None
            st.frozen = False
        if st.dropped_two_strikes:
            continue  # permanently blocked after the second stop-loss strike
        if st.cooldown_until is not None and d <= st.cooldown_until:
            continue  # 90-day cooling-off period after LAGGARD_EXIT
        if st.cooldown_until is not None and not golden_cross:
            continue  # broken stock stays blocked until trend reclaimed
        if st.cooldown_until is not None and d > st.cooldown_until and golden_cross:
            st.cooldown_until = None
            st.frozen = False
        before_q = st.qty
        # Hard guardrail: total buy deduction (amount + 0.25% friction) must never
        # exceed cash on hand, eliminating phantom negative-cash leverage.
        max_amt = cash / (1.0 + FRICTION_RATE) if cash > 0 else 0.0
        amt = min(amt, max_amt)
        if amt <= 0:
            continue
        friction = _execute_buy(st, d, amt, px)
        if st.qty > before_q + 1e-12:
            cash -= amt
            stats["friction"] += friction
    _note_cash_idle(stats, cash, len(eligible_mults), budget,
                    _finite(alloc.get("total_allocated"), 0.0) or 0.0)
    return cash


# --------------------------------------------------------------------------- #
# Summary helpers
# --------------------------------------------------------------------------- #
def _config_dict(config: BacktestConfig) -> dict:
    return {
        "years": config.years,
        "include_watchlist": bool(config.include_watchlist),
        "include_closed_positions": bool(config.include_closed_positions),
        "base_unit_sip": float(config.base_unit_sip),
        "max_monthly_ceiling": float(config.max_monthly_ceiling),
        "trim_aer": float(config.trim_aer),
        "trim_target_mult": float(config.trim_target_mult),
        "enable_quarantine_reentry": bool(getattr(config, "enable_quarantine_reentry", False)),
    }


def _max_drawdown(values) -> float:
    peak = float("-inf")
    mdd = 0.0
    for v in values:
        v = _finite(v)
        if v is None:
            continue
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            mdd = max(mdd, dd)
    return mdd


def _calendar_year_returns(monthly_curve, contributions,
                           year_trading_days=None) -> dict:
    """Dietz-style calendar-year returns from a monthly value curve.

    The opening calendar year of a backtest window (e.g. Nov-Dec 2019) often has
    fewer than 60 trading days, which skews a full-year Dietz return. Such a year
    is kept (never dropped) but keyed ``"2019 (Partial)"`` so the annual table is
    transparent about the short coverage.
    """
    series = [(_to_date(m["date"]), m["value"]) for m in monthly_curve]
    series = [(d, v) for d, v in series if d is not None and v is not None]
    if not series:
        return {}
    out: dict = {}
    prev_end = None
    years = sorted({d.year for d, _ in series})
    for i, y in enumerate(years):
        yr = [(d, v) for d, v in series if d.year == y]
        if not yr:
            continue
        v_start = prev_end if prev_end is not None else yr[0][1]
        v_end = yr[-1][1]
        flow = sum(amt for d, amt in contributions if d.year == y)
        denom = v_start + 0.5 * flow
        ret = ((v_end - v_start - flow) / denom) if denom > 0 else 0.0
        partial = (i == 0 and year_trading_days
                   and year_trading_days.get(y, 0) < 60)
        key = f"{y} (Partial)" if partial else str(y)
        out[key] = _pct(ret)
        prev_end = v_end
    return out


def _build_sell_breakdown(stats: dict) -> dict:
    """Build the diagnostic per-trigger sell table from ``stats`` (no logic change).

    Every trigger in :data:`SELL_REASON_ORDER` is emitted even when it never
    fired (count 0, gross 0.0) so the audit table is unambiguous.
    """
    rows = []
    total_count = 0
    total_gross = 0.0
    total_net = 0.0
    for reason in SELL_REASON_ORDER:
        bucket = stats.get("sells", {}).get(reason) or {
            "count": 0, "gross": 0.0, "net": 0.0, "tax": 0.0, "friction": 0.0}
        rows.append({
            "reason": reason,
            "count": bucket["count"],
            "gross": round(bucket["gross"], 2),
            "net": round(bucket["net"], 2),
            "tax": round(bucket["tax"], 2),
            "friction": round(bucket["friction"], 2),
        })
        # Totals are summed from the UNROUNDED buckets so the TOTAL row matches
        # the scorecard realized-sells figure to the paisa.
        total_count += bucket["count"]
        total_gross += bucket["gross"]
        total_net += bucket["net"]
    return {
        "rows": rows,
        "total_trades": total_count,
        "total_gross_proceeds": round(total_gross, 2),
        "total_net_proceeds": round(total_net, 2),
    }


def _build_attribution(states, prices: PriceIndex, end: date) -> list:
    rows = []
    for st in states:
        if st.invested <= 0 and st.qty <= 1e-9 and st.proceeds <= 0:
            continue
        last = prices.price_on(st.ticker, end) or st.avg
        cur_val = st.qty * last
        total_value = cur_val + st.proceeds
        gain_pct = ((total_value / st.invested - 1.0) * 100.0) if st.invested > 0 else None
        rows.append({
            "ticker": st.ticker,
            "invested": round(st.invested, 2),
            "current_value": round(cur_val, 2),
            "realized_proceeds": round(st.proceeds, 2),
            "gain_pct": round(gain_pct, 2) if gain_pct is not None else None,
            "trades": int(st.trades),
            "status": "closed" if st.qty <= 1e-9 else "open",
        })
    rows.sort(key=lambda r: (r["gain_pct"] is None, -(r["gain_pct"] or 0.0)))
    return rows


def _empty_result(config: BacktestConfig, sim_tickers) -> dict:
    return {
        "config": _config_dict(config),
        "universe": {"sim_tickers": list(sim_tickers),
                     "benchmarks": list(bd.BENCHMARK_TICKERS)},
        "period": None,
        "daily_curve": [],
        "monthly_curve": [],
        "cashflows": [],
        "scorecard": {
            "invested": 0.0, "final_net_value": 0.0, "realized_sells": 0.0,
            "post_tax_xirr_pct": None, "pre_tax_xirr_pct": None,
            "max_drawdown_pct": 0.0, "return_drawdown_ratio": None,
            "taxes_paid": 0.0, "friction_paid": 0.0, "trade_count": 0,
        },
        "calendar_year_returns": {},
        "attribution": [],
        "benchmarks": {},
        "sell_breakdown": {"rows": [], "total_trades": 0,
                           "total_gross_proceeds": 0.0,
                           "total_net_proceeds": 0.0},
        "aer_stats": {"count": 0, "min": None, "median": None, "max": None},
        "cash_idle": {"total": 0, "no_eligible": 0,
                      "budget_limited": 0, "per_stock_caps": 0},
        "lifecycle": {"quarantined_count": 0, "reentered_count": 0, "dropped_two_strikes": 0,
                      "dropped_tickers": []},
    }


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def run_backtest(config: BacktestConfig | None = None, market_db_path=None,
                 txs=None, holdings=None, universe_result=None) -> dict:
    """Run one walk-forward simulation and return a JSON-serialisable result."""
    if config is None:
        config = BacktestConfig(market_db_path=market_db_path)
    db = bd._default_db(market_db_path or config.market_db_path)

    if universe_result is None:
        universe_result = bd.load_backtest_universe(
            market_db_path=db, txs=txs, holdings=holdings,
            backtest_start=config.start_date)
    ur = universe_result

    # ---- Select the simulation universe (respecting include_* flags) ----
    sources = _classify_universe(ur.tickers, txs, holdings, db)
    bench_set = set(bd.BENCHMARK_TICKERS) | set(bd.BENCHMARK_FALLBACKS.values())
    sim_tickers = []
    for t in ur.tickers:
        if t in bench_set:
            continue
        src = sources.get(t, set())
        if "active" in src or "watchlist" in src and config.include_watchlist or "closed" in src and config.include_closed_positions:
            sim_tickers.append(t)
    sim_tickers = sorted(set(sim_tickers))

    prices = PriceIndex(set(sim_tickers) | bench_set, db,
                        isin_by_ticker=ur.isin_by_ticker)
    all_dates = prices.all_dates(set(sim_tickers) | bench_set)
    if not all_dates or not sim_tickers:
        return _empty_result(config, sim_tickers)

    data_end = all_dates[-1]
    data_start = all_dates[0]
    backtest_start = _to_date(config.start_date) or _years_before(data_end, config.years)
    backtest_start = max(backtest_start, data_start)
    window_dates = [d for d in all_dates if backtest_start <= d <= data_end]
    if not window_dates:
        return _empty_result(config, sim_tickers)

    # ---- Initialise per-stock state (activation gated by first_buy + 200-DMA) ----
    fb_map = getattr(ur, "first_buy_date", None) or {}
    states = [
        _StockState(
            ticker=t,
            isin=ur.isin_by_ticker.get(t),
            first_buy_date=_to_date(fb_map.get(t)),
            sources=sources.get(t, set()),
        )
        for t in sim_tickers
    ]
    for st in states:
        not_before = st.first_buy_date or backtest_start
        # Prospective watchlist additions may only activate in the most recent
        # 3 years (end_date - 3y) regardless of the selected backtest window — never
        # back-date them into 2019-2022. Still subject to the 200-DMA warm-up.
        if "watchlist" in (st.sources or set()):
            not_before = max(not_before, _years_before(data_end, WATCHLIST_MAX_YEARS))
        st.activation_date = prices.first_ready_date(st.ticker, max(not_before, backtest_start))

    # First trading day of each calendar month within the window.
    monthly_days: set[date] = set()
    seen_ym: set[tuple[int, int]] = set()
    for d in window_dates:
        key = (d.year, d.month)
        if key not in seen_ym:
            seen_ym.add(key)
            monthly_days.add(d)

    tax_state = {"ltcg_by_fy": {}}
    stats = {"friction": 0.0, "taxes": 0.0, "sells": {}}
    contributions: list = []
    cash = 0.0
    daily_curve: list = []
    # Per-trading-day diagnostic snapshots kept in lockstep with ``daily_curve``:
    # ``(active_holdings_count, eligible_sip_stocks_count)``.
    daily_counts: list = []

    # Real-ledger net buy inflow per calendar month (floored at zero downstream).
    monthly_net = _monthly_net_buy_inflow(txs)
    rd = {"pending": 0.0, "release": {}, "nifty500_peak": 0.0}

    for d in window_dates:
        nifty500_px = prices.price_on("^CRSLDX", d)
        if nifty500_px is not None and nifty500_px > 0:
            rd["nifty500_peak"] = max(rd.get("nifty500_peak", 0.0), nifty500_px)
        if d in monthly_days:
            bx = _benchmark_xirr(prices, d, backtest_start)
            cash = _monthly_step(states, prices, d, cash, config, tax_state,
                                 bx, contributions, stats,
                                 monthly_inflow=monthly_net.get((d.year, d.month), 0.0), rd=rd)
        else:
            cash = _intraday_gtt(states, prices, d, cash, tax_state, stats, rd=rd)
        cash = max(cash, 0.0)  # hard floor: no phantom negative-cash leverage
        holdings_value = sum(
            st.qty * (prices.price_on(st.ticker, d) or st.avg)
            for st in states if st.qty > 1e-9)
        # Diagnostic snapshots at each trading day: how many stocks were held
        # (qty > 0) and how many were eligible for a fresh SIP (sip_multiplier > 0).
        daily_curve.append((d, cash + holdings_value, cash))
        daily_counts.append((
            sum(1 for st in states if st.qty > 1e-9),
            sum(1 for st in states if st.sip_multiplier > 0),
        ))

    # ---- Terminal valuation & scorecard ----
    final_holdings = sum(
        st.qty * (prices.price_on(st.ticker, data_end) or st.avg)
        for st in states if st.qty > 1e-9)
    final_value = cash + final_holdings
    invested_total = sum(st.invested for st in states)
    total_taxes = stats["taxes"]
    total_friction = stats["friction"]

    contrib_cf = [(d, -amt) for d, amt in contributions]
    invested_total = sum(amt for _d, amt in contributions)
    post_xirr = compute_xirr(contrib_cf + [(data_end, final_value)])
    pre_xirr = compute_xirr(contrib_cf + [(data_end, final_value + total_taxes)])
    mdd = _max_drawdown([v for _d, v, _c in daily_curve])
    total_return = (final_value / invested_total - 1.0) if invested_total > 0 else None
    # Never leave the ratio None: with no drawdown it collapses to the raw return.
    if mdd > 0:
        rd_ratio = (total_return / mdd) if total_return is not None else None
    elif total_return is not None and total_return > 0:
        rd_ratio = total_return
    else:
        rd_ratio = 0.0 if total_return is not None else None

    scorecard = {
        "invested": round(invested_total, 2),
        "final_net_value": round(_finite(final_value, 0.0) or 0.0, 2),
        "realized_sells": round(sum(st.gross_proceeds for st in states), 2),
        "post_tax_xirr_pct": _pct(post_xirr),
        "pre_tax_xirr_pct": _pct(pre_xirr),
        "max_drawdown_pct": _pct(mdd),
        "return_drawdown_ratio": round(rd_ratio, 4) if rd_ratio is not None else None,
        "taxes_paid": round(total_taxes, 2),
        "friction_paid": round(total_friction, 2),
        "trade_count": int(sum(st.trades for st in states)),
        "final_cash": round(cash, 2),
        "final_holdings_value": round(final_holdings, 2),
    }

    # ---- Curves ----
    daily_out = []
    for (d, v, c), (hc, ec) in zip(daily_curve, daily_counts):
        daily_out.append({
            "date": d.isoformat(), "value": round(v, 2), "cash": round(c, 2),
            "holdings_count": int(hc), "eligible_sip_count": int(ec),
        })
    monthly_out: list = []
    for d, v, c in daily_curve:
        if monthly_out and monthly_out[-1]["_ym"] == (d.year, d.month):
            monthly_out[-1].update({"date": d.isoformat(), "value": round(v, 2),
                                    "cash": round(c, 2)})
        else:
            monthly_out.append({"_ym": (d.year, d.month), "date": d.isoformat(),
                                "value": round(v, 2), "cash": round(c, 2)})
    for m in monthly_out:
        m.pop("_ym", None)

    # ---- Exact cash-flow matched benchmarks (same rupees, same dates) ----
    flows = [(d, -amt) for d, amt in contributions]
    n50_t = _resolve_benchmark(prices, [bd.NIFTY50_TICKER, bd.NIFTY50_FALLBACK])
    n500_t = _resolve_benchmark(prices, list(bd.NIFTY500_TICKERS))
    # Equal-Weight benchmark uses the real portfolio (active + closed) only, gated
    # by each stock's true discovery date; watchlist/prospective names are excluded.
    ew_tickers = [t for t in sim_tickers
                  if ("active" in sources.get(t, set())) or ("closed" in sources.get(t, set()))]
    ew_first_buy = {t: _to_date(fb_map.get(t)) for t in ew_tickers if _to_date(fb_map.get(t))}
    # Trading-day counts per calendar year (for partial opening-year labelling).
    year_trading_days: dict[int, int] = {}
    for d in window_dates:
        year_trading_days[d.year] = year_trading_days.get(d.year, 0) + 1
    benchmarks = {
        "equal_weight": _sim_equal_weight_benchmark(
            prices, ew_tickers, flows, window_dates, contributions,
            year_trading_days, backtest_start, data_end, first_buy_map=ew_first_buy),
        "nifty50": _sim_index_benchmark(
            prices, n50_t, flows, window_dates, contributions,
            year_trading_days, backtest_start, data_end,
            dividend_yield=INDEX_DIVIDEND_YIELD),
        "nifty500": _sim_index_benchmark(
            prices, n500_t, flows, window_dates, contributions,
            year_trading_days, backtest_start, data_end,
            dividend_yield=INDEX_DIVIDEND_YIELD),
    }
    invested_out = [_flow_out(d, amt) for d, amt in contributions]
    for b in benchmarks.values():
        b["cashflows"] = list(invested_out)  # identical rupees+dates across all benchmarks

    return {
        "config": _config_dict(config),
        "universe": {"sim_tickers": sim_tickers,
                     "benchmarks": {"nifty50": n50_t, "nifty500": n500_t},
                     "activations": {st.ticker: st.activation_date.isoformat()
                                     for st in states if st.activation_date is not None},
                     "skipped_tickers": sorted(set(getattr(ur, "skipped_tickers", []) or []))},
        "period": {"start": backtest_start.isoformat(), "end": data_end.isoformat()},
        "daily_curve": daily_out,
        "monthly_curve": monthly_out,
        "cashflows": invested_out,
        "scorecard": scorecard,
        "sell_breakdown": _build_sell_breakdown(stats),
        "aer_stats": _aer_stats(stats.get("aer_samples", [])),
        "cash_idle": stats.get("cash_idle") or {
            "total": 0, "no_eligible": 0, "budget_limited": 0, "per_stock_caps": 0},
        "crash_freeze": stats.get("crash_freeze") or {"months": [], "nifty_dd": None, "triggered": 0,
                                                      "suppressed_gtt": 0, "suppressed_laggard": 0,
                                                      "drawdowns": []},
        "lifecycle": stats.get("lifecycle") or {"quarantined_count": 0, "reentered_count": 0, "dropped_two_strikes": 0,
                                            "dropped_tickers": []},
        "calendar_year_returns": _calendar_year_returns(monthly_out, contributions,
                                                        year_trading_days),
        "attribution": _build_attribution(states, prices, data_end),
        "benchmarks": benchmarks,
    }


