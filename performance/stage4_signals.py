"""Stage 4 - formula-driven Buy / SIP / Profit-Booking / Stop-Loss engine.

Pure math and signal generation only (no UI, no external technical-indicator
APIs). All technical signals are derived from the local daily *Adjusted Close*
series stored in ``stock_prices`` (``adjusted_close`` column, falling back to
``close_price`` when an adjusted value is unavailable).

Key entry points
----------------
``build_stock_input``        assemble a :class:`StockInput` from raw transactions.
``analyzeStockSignals``      per-stock technical + allocation metrics.
``evaluate_signals``         priority-ordered sell / profit-booking signals.
``compute_sip_multiplier``   monthly SIP multiplier for one stock.
``rank_bottom_laggards``     Bottom-N holdings by Efficiency Score.

Everything is deterministic and unit-testable: pass a plain ``price_lookup``
(or a :class:`StockInput` with a pre-built ``price_series``) and the pure
functions do the rest.
"""
from __future__ import annotations

import math
import os
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import pairwise

# --------------------------------------------------------------------------- #
# Tunable constants (single source of truth for the whole stage)
# --------------------------------------------------------------------------- #
MIN_TRADING_DAYS = 260        # requirement before signals are trusted
DMA_SHORT = 50
DMA_LONG = 200
VOL_WINDOW = 252              # trailing trading days for annualised volatility
TRADING_DAYS_PER_YEAR = 252

STOP_LOSS_FLOOR = 0.20
STOP_LOSS_CAP = 0.45
STOP_LOSS_MULT = 1.5

HARD_STOP_MIN_INVESTED = 150000.0
HARD_STOP_AER_FLOOR = 0.50

TREND_BREAK_AER = 1.30
TREND_BREAK_DMA_BUFFER = 0.97

TRIM_AER = 1.50
TRIM_MIN_MARKET_VALUE = 200000.0
TRIM_MIN_XIRR = 0.25
TRIM_TARGET_MULT = 1.25

LAGGARD_MIN_DAYS = 365

# Signal identifiers (also used as the "action" family)
HARD_STOP_LOSS = "HARD_STOP_LOSS"
TREND_BREAK_LOCK = "TREND_BREAK_LOCK"
CONCENTRATION_TRIM = "CONCENTRATION_TRIM"
LAGGARD_EXIT = "LAGGARD_EXIT"
NO_SIGNAL = "NONE"

FROZEN_SIGNALS = (HARD_STOP_LOSS, TREND_BREAK_LOCK)


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #
@dataclass
class StockInput:
    """One holding fed into the signal engine."""

    security_id: str
    symbol: str = ""
    quantity: float = 0.0
    avg_buy_price: float = 0.0
    invested_cost: float = 0.0
    market_value: float | None = None
    xirr: float | None = None
    holding_start_date: date | None = None
    price_series: list[float] = field(default_factory=list)

    @property
    def cmp(self) -> float | None:
        """Current Market Price = latest Adjusted Close in the series."""
        return self.price_series[-1] if self.price_series else None


@dataclass
class PortfolioTotals:
    """Portfolio-level aggregates used for weighting."""

    total_invested_cost: float = 0.0
    total_market_value: float = 0.0


@dataclass
class SignalResult:
    """Outcome of evaluating one stock's priority-ordered signal."""

    security_id: str
    signal_type: str = NO_SIGNAL
    priority: int = 99
    action: str = "HOLD"
    sell_pct: float = 0.0
    target_sell_rupees: float | None = None
    shares_to_sell: int | None = None
    sip_multiplier: float = 0.0
    reason: str = ""
    metrics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "security_id": self.security_id,
            "signal_type": self.signal_type,
            "priority": self.priority,
            "action": self.action,
            "sell_pct": self.sell_pct,
            "target_sell_rupees": self.target_sell_rupees,
            "shares_to_sell": self.shares_to_sell,
            "sip_multiplier": self.sip_multiplier,
            "reason": self.reason,
            **self.metrics,
        }



# --------------------------------------------------------------------------- #
# Small numeric helpers (all pure)
# --------------------------------------------------------------------------- #
def _to_date(v) -> date:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()
    raise ValueError("bad date")


def _safe_div(num, den):
    try:
        num = float(num)
        den = float(den)
    except (TypeError, ValueError):
        return None
    if den == 0:
        return None
    return num / den


def _sma(values, window) -> float | None:
    """Simple moving average of the last ``window`` values (numpy-free)."""
    if not values or window <= 0 or len(values) < window:
        return None
    tail = values[-window:]
    return sum(tail) / float(window)


def _daily_log_returns(prices) -> list[float]:
    """Daily logarithmic returns ``ln(P_t / P_{t-1})``."""
    out: list[float] = []
    for prev, cur in pairwise(prices):
        if prev is not None and cur is not None and prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def _annualized_volatility(returns) -> float | None:
    """Sample std-dev (ddof=1) of daily log returns x sqrt(252)."""
    clean = [r for r in returns if r is not None]
    if len(clean) < 2:
        return None
    return statistics.stdev(clean) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _dynamic_stop_loss_pct(annual_vol: float | None) -> float | None:
    """1.5 x volatility clamped to a [20%, 45%] band."""
    if annual_vol is None:
        return None
    return min(STOP_LOSS_CAP, max(STOP_LOSS_FLOOR, STOP_LOSS_MULT * annual_vol))


# --------------------------------------------------------------------------- #
# Core per-stock metric function
# --------------------------------------------------------------------------- #
def analyzeStockSignals(stock: StockInput, portfolioTotals: PortfolioTotals,
                        benchmarkXIRR: float | None) -> dict:
    """Compute the full metric bundle for one stock.

    Returns a dict with the 50/200-DMA, annualised volatility, dynamic
    stop-loss %, cost / market weight, AER and Efficiency Score plus a few
    bookkeeping fields (``trading_days`` and ``insufficient_data``).
    """
    series = [float(x) for x in (stock.price_series or []) if x is not None]
    n = len(series)

    dma_short = _sma(series, DMA_SHORT)
    dma_long = _sma(series, DMA_LONG)

    returns_window = series[-(VOL_WINDOW + 1):]
    annual_vol = _annualized_volatility(_daily_log_returns(returns_window))
    stop_loss = _dynamic_stop_loss_pct(annual_vol)

    cost_weight = _safe_div(stock.invested_cost, portfolioTotals.total_invested_cost)
    market_weight = _safe_div(stock.market_value, portfolioTotals.total_market_value)
    aer = _safe_div(market_weight, cost_weight) if cost_weight else None
    efficiency = None
    if stock.xirr is not None and benchmarkXIRR is not None and aer is not None:
        efficiency = (float(stock.xirr) - float(benchmarkXIRR)) * aer

    return {
        "security_id": stock.security_id,
        "symbol": stock.symbol or stock.security_id,
        "cmp": stock.cmp,
        "dma_50": dma_short,
        "dma_200": dma_long,
        "annual_volatility": annual_vol,
        "dynamic_stop_loss_pct": stop_loss,
        "cost_weight_pct": cost_weight,
        "market_weight_pct": market_weight,
        "aer": aer,
        "efficiency_score": efficiency,
        "trading_days": n,
        "insufficient_data": n < MIN_TRADING_DAYS,
    }


# --------------------------------------------------------------------------- #
# Priority-ordered sell / profit-booking signals
# --------------------------------------------------------------------------- #
def _held_days(stock: StockInput, as_of_date) -> int | None:
    if stock.holding_start_date is None or as_of_date is None:
        return None
    try:
        return (_to_date(as_of_date) - _to_date(stock.holding_start_date)).days
    except (TypeError, ValueError):
        return None


def evaluate_signal(stock: StockInput, metrics: dict, portfolioTotals: PortfolioTotals,
                    as_of_date=None) -> SignalResult:
    """Apply the four signal rules in strict priority order for one stock."""
    cmp = metrics.get("cmp")
    aer = metrics.get("aer")
    dma_short = metrics.get("dma_50")
    dma_long = metrics.get("dma_200")
    stop_loss = metrics.get("dynamic_stop_loss_pct")
    cost_weight = metrics.get("cost_weight_pct")
    efficiency = metrics.get("efficiency_score")
    sid = stock.security_id

    def _result(signal_type, priority, action, sell_pct=0.0,
                target=None, shares=None, reason=""):
        return SignalResult(
            security_id=sid, signal_type=signal_type, priority=priority, action=action,
            sell_pct=sell_pct, target_sell_rupees=target, shares_to_sell=shares,
            reason=reason, metrics=metrics,
        )

    # Priority 1 -- HARD_STOP_LOSS --------------------------------------------
    if stock.invested_cost is not None and stock.invested_cost >= HARD_STOP_MIN_INVESTED:
        stop_hit = (
            cmp is not None and stop_loss is not None
            and stock.avg_buy_price > 0
            and cmp <= stock.avg_buy_price * (1.0 - stop_loss)
        )
        aer_hit = aer is not None and aer <= HARD_STOP_AER_FLOOR
        if stop_hit or aer_hit:
            why = "price hit dynamic stop-loss" if stop_hit else "AER collapsed below 0.50"
            return _result(HARD_STOP_LOSS, 1, "SELL_100_FREEZE_SIP", 1.0,
                           reason="HARD_STOP_LOSS: " + why)

    # Priority 2 -- TREND_BREAK_LOCK ------------------------------------------
    if (aer is not None and aer >= TREND_BREAK_AER and cmp is not None
            and dma_short is not None and cmp < dma_short * TREND_BREAK_DMA_BUFFER):
        return _result(TREND_BREAK_LOCK, 2, "SELL_50_LOCK_GAINS", 0.5,
                       reason="TREND_BREAK_LOCK: AER>=1.30 and price broke below 50-DMA")

    # Priority 3 -- CONCENTRATION_TRIM ----------------------------------------
    if (aer is not None and aer >= TRIM_AER
            and stock.market_value is not None and stock.market_value >= TRIM_MIN_MARKET_VALUE
            and stock.xirr is not None and stock.xirr > TRIM_MIN_XIRR):
        target = None
        shares = None
        if cost_weight is not None and cmp is not None and cmp > 0:
            target = stock.market_value - (
                TRIM_TARGET_MULT * cost_weight * portfolioTotals.total_market_value
            )
            shares = math.floor(target / cmp) if target is not None and target > 0 else 0
        return _result(CONCENTRATION_TRIM, 3, "TRIM_TO_1.25X_COST_WEIGHT", 0.0,
                       target=target, shares=shares,
                       reason="CONCENTRATION_TRIM: AER>=1.50, big winner, overweight")

    # Priority 4 -- LAGGARD_EXIT ----------------------------------------------
    held = _held_days(stock, as_of_date)
    if (held is not None and held > LAGGARD_MIN_DAYS and efficiency is not None
            and efficiency < 0 and cmp is not None and dma_long is not None
            and cmp < dma_long):
        return _result(LAGGARD_EXIT, 4, "REVIEW_EXIT",
                       reason="LAGGARD_EXIT: held >1y, below benchmark & under 200-DMA")

    return _result(NO_SIGNAL, 99, "HOLD", reason="No sell rule triggered")


# --------------------------------------------------------------------------- #
# Monthly SIP multiplier
# --------------------------------------------------------------------------- #
def compute_sip_multiplier(stock: StockInput, metrics: dict, benchmarkXIRR: float | None,
                           frozen: bool = False) -> float:
    """Return the monthly SIP multiplier (0.0 / 0.5 / 1.0 / 2.0)."""
    if frozen:
        return 0.0
    cmp = metrics.get("cmp")
    aer = metrics.get("aer")
    dma_short = metrics.get("dma_50")
    dma_long = metrics.get("dma_200")
    xirr = stock.xirr
    if cmp is None or dma_long is None or aer is None:
        return 0.0

    # 2.0x -- Press Winners
    if (xirr is not None and benchmarkXIRR is not None and xirr > benchmarkXIRR + 0.10
            and cmp > dma_long and 1.05 <= aer < 1.40):
        return 2.0
    # 1.0x -- Core Accumulate
    if cmp > dma_long and 0.85 <= aer < 1.25:
        return 1.0
    # 0.5x -- Overextended Caution
    if aer >= 1.25 and dma_short is not None and cmp >= dma_short * TREND_BREAK_DMA_BUFFER:
        return 0.5
    # 0.0x -- Trend Freeze (cmp < 200-DMA or AER < 0.85, plus any residual gap)
    return 0.0



# --------------------------------------------------------------------------- #
# Orchestrator: metrics + signals + SIP multipliers for a whole portfolio
# --------------------------------------------------------------------------- #
def evaluate_signals(stocks, portfolioTotals: PortfolioTotals,
                     benchmarkXIRR: float | None, as_of_date=None) -> list[dict]:
    """Evaluate every stock and return enriched, priority-annotated rows."""
    rows: list[dict] = []
    for stock in stocks or []:
        metrics = analyzeStockSignals(stock, portfolioTotals, benchmarkXIRR)
        sig = evaluate_signal(stock, metrics, portfolioTotals, as_of_date)
        frozen = sig.signal_type in FROZEN_SIGNALS
        sig.sip_multiplier = compute_sip_multiplier(stock, metrics, benchmarkXIRR, frozen=frozen)
        row = sig.as_dict()
        row["holding_start_date"] = (
            str(stock.holding_start_date) if stock.holding_start_date else None
        )
        row["is_bottom_laggard"] = False
        rows.append(row)

    # Flag the Bottom-5 laggards by Efficiency Score (ascending).
    for r in rank_bottom_laggards(rows, n=5):
        r["is_bottom_laggard"] = True
    return rows


def rank_bottom_laggards(rows, n: int = 5) -> list[dict]:
    """Return the ``n`` holdings with the lowest Efficiency Score."""
    ranked = sorted(
        [r for r in rows or [] if r.get("efficiency_score") is not None],
        key=lambda r: r["efficiency_score"],
    )
    return ranked[:n]


# --------------------------------------------------------------------------- #
# Build a StockInput from raw transactions + local Adjusted Close series
# --------------------------------------------------------------------------- #
def _tx_isin(t: dict) -> str:
    return str(t.get("isin") or t.get("security") or t.get("symbol") or "").strip().upper()


def _tx_side(t: dict) -> str:
    return str(t.get("buy_sell") or t.get("side") or "").strip().upper()


def _tx_date(t: dict):
    return t.get("trade_date") or t.get("settlement_date") or t.get("date")


def _tx_qty(t: dict) -> float:
    try:
        return float(t.get("quantity") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _tx_amount(t: dict) -> float:
    try:
        return abs(float(t.get("net_amount") or t.get("amount") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _fifo_position(transactions, security_id, as_of_date, opening_positions=None):
    """FIFO lots for the *currently held* shares.

    Returns ``(quantity, cost_basis, avg_buy_price, holding_start_date)`` where
    ``holding_start_date`` is the first buy of the current continuous open
    episode (it resets after a full exit and subsequent re-entry).
    """
    sid = str(security_id).strip().upper()
    cutoff = _to_date(as_of_date)
    events = []

    for op in opening_positions or []:
        try:
            od = _to_date(
                op.get("ledger_opening_date") if isinstance(op, dict) else op.ledger_opening_date
            )
        except (TypeError, ValueError):
            continue
        if od > cutoff:
            continue
        opis = str(
            (op.get("security_id") if isinstance(op, dict) else op.security_id) or ""
        ).strip().upper()
        if opis != sid:
            continue
        oq = float((op.get("opening_qty") if isinstance(op, dict) else op.opening_qty) or 0.0)
        events.append((od, "BUY", oq, 0.0))  # opening cost unknown -> 0

    for t in transactions or []:
        if _tx_isin(t) != sid:
            continue
        try:
            dd = _to_date(_tx_date(t))
        except (TypeError, ValueError):
            continue
        if dd > cutoff:
            continue
        side = _tx_side(t)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            events.append((dd, "BUY", _tx_qty(t), _tx_amount(t)))
        elif side == "SELL":
            events.append((dd, "SELL", _tx_qty(t), 0.0))

    events.sort(key=lambda x: (x[0], x[1]))
    lots: list[list[float]] = []  # [qty, unit_price]
    running = 0.0
    episode_start = None
    for dd, kind, q, amt in events:
        if kind == "BUY":
            unit = (amt / q) if q > 0 and amt else 0.0
            if running <= 1e-9:
                episode_start = dd
            lots.append([q, unit])
            running += q
        else:  # SELL consumes oldest lots
            remaining = q
            while remaining > 1e-9 and lots:
                take = min(lots[0][0], remaining)
                lots[0][0] -= take
                remaining -= take
                if lots[0][0] <= 1e-9:
                    lots.pop(0)
            running -= q
            if running <= 1e-9:
                running = 0.0
                episode_start = None

    qty = sum(lot[0] for lot in lots)
    cost = sum(lot[0] * lot[1] for lot in lots)
    avg = (cost / qty) if qty > 1e-9 else 0.0
    return qty, cost, avg, episode_start



# --------------------------------------------------------------------------- #
# Adjusted Close series + StockInput construction from raw transactions
# --------------------------------------------------------------------------- #
def _default_adjusted_close_series(security_id, as_of_date, db_path=None) -> list[float]:
    """Ascending Adjusted Close series (``<= as_of_date``) from the local DB."""
    db = (db_path or os.environ.get("ICICI_MARKET_DB")
          or os.environ.get("DEFAULT_MARKET_DB") or "data/market_data.sqlite3")
    sid = str(security_id).strip().upper()
    cutoff = str(_to_date(as_of_date))[:10]
    try:
        con = sqlite3.connect(db)
        rows = con.execute(
            "SELECT COALESCE(adjusted_close, close_price) FROM stock_prices "
            "WHERE UPPER(security_id)=? AND trade_date<=? ORDER BY trade_date",
            (sid, cutoff),
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    return [float(r[0]) for r in rows if r[0] is not None]


def make_adjusted_close_lookup(db_path=None):
    """Return ``price_lookup(sid, date)`` backed by Adjusted Close.

    The returned callable also exposes ``price_lookup.series(sid, as_of_date)``
    returning the full ascending series up to ``as_of_date`` so
    :func:`build_stock_input` can reuse it without re-querying.
    """

    def _price(sid, dt):
        series = _default_adjusted_close_series(sid, dt, db_path)
        return series[-1] if series else None

    _price.series = lambda sid, ad: _default_adjusted_close_series(sid, ad, db_path)  # type: ignore[attr-defined]
    return _price


def build_stock_input(transactions, price_lookup, as_of_date, security_id,
                      dividends=None, opening_positions=None, series=None) -> StockInput:
    """Assemble a :class:`StockInput` from raw transactions.

    Reuses :func:`ledger.positions.reconstruct` (quantity), a FIFO cost basis and
    :func:`performance.equity_analytics.stock_xirr` (XIRR).  The price series is
    taken from, in order: the ``series`` argument, ``price_lookup.series(...)``
    when available, or the local Adjusted Close table.
    """
    from ledger.positions import reconstruct
    from performance import equity_analytics as ea

    sid = str(security_id).strip().upper()
    ad = _to_date(as_of_date)

    if series is None:
        provider = getattr(price_lookup, "series", None)
        if callable(provider):
            series = provider(sid, ad)
        else:
            series = _default_adjusted_close_series(sid, ad)
    series = [float(x) for x in (series or []) if x is not None]

    cmp = series[-1] if series else None
    if cmp is None and callable(price_lookup):
        try:
            cmp = price_lookup(sid, ad)
        except Exception:  # noqa: BLE001
            cmp = None

    qty_fifo, cost_basis, avg_buy, episode_start = _fifo_position(
        transactions, sid, ad, opening_positions
    )
    try:
        qty_hold = reconstruct(transactions, ad, opening_positions=opening_positions).get(sid, 0.0)
    except Exception:  # noqa: BLE001
        qty_hold = qty_fifo
    quantity = float(max(qty_fifo, qty_hold))

    market_value = (quantity * cmp) if cmp is not None else None

    try:
        xirr = ea.stock_xirr(
            transactions, dividends or [], sid, None, ad,
            terminal_value=market_value, terminal_date=ad,
        )
    except Exception:  # noqa: BLE001
        xirr = None

    return StockInput(
        security_id=sid,
        symbol=sid,
        quantity=quantity,
        avg_buy_price=avg_buy,
        invested_cost=cost_basis,
        market_value=market_value,
        xirr=xirr,
        holding_start_date=episode_start,
        price_series=series,
    )


def build_portfolio_totals(stocks) -> PortfolioTotals:
    """Sum invested cost and market value across :class:`StockInput` objects."""
    total_cost = 0.0
    total_mv = 0.0
    for s in stocks or []:
        total_cost += float(s.invested_cost or 0.0)
        total_mv += float(s.market_value or 0.0)
    return PortfolioTotals(total_invested_cost=total_cost, total_market_value=total_mv)

