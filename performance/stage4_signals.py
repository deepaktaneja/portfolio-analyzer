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
TREND_BREAK_DMA_BUFFER = 0.95          # 5% below 50-DMA (monthly check-in, fewer false alarms)
SIP_OVEREXTENDED_DMA_BUFFER = 0.97     # SIP 0.5x rule keeps the original 97% threshold

TRIM_AER = 1.85
TRIM_MIN_MARKET_VALUE = 200000.0
TRIM_MIN_XIRR = 0.25
TRIM_TARGET_MULT = 1.40

INCUBATION_MAX_INVESTED = 100000.0     # below this => incubating
INCUBATION_MAX_DAYS = 180               # or under 180 days since first buy => incubating
INCUBATION_MAX_AGE_DAYS = 365           # or under 365 days since first buy => incubating

MIN_PRICE_HISTORY_DAY = 200   # fewer than this many trading days => missing price history badge
INCUBATION_GTT_MULT = 0.60             # incubation GTT = 40% catastrophe circuit breaker
GTT_BUFFER_WARN = 0.10                 # warn when within 10% of GTT trigger

MAX_SINGLE_STOCK_WEIGHT_PCT = 0.08     # over-allocated freeze target (8% market weight)
SIP_GRANULARITY = 5000.0               # round all SIPs to exact multiples of Rs 5,000
TRAILING_PEAK_MULT = 2.00              # activate trailing peak floor only after a +100% (2.0x) rally
TRAILING_STOP_MULT = 1.50              # trailing stop width = 1.50 x annual vol
TRAILING_STOP_FLOOR = 0.35             # clamp lower bound for trailing stop %
TRAILING_STOP_CAP = 0.45               # clamp upper bound for trailing stop %

# Monthly-SIP rule thresholds, keyed off dma200_distance_pct = (CMP - 200-DMA) / 200-DMA
SIP_RUN_UP_DMA_DISTANCE = 0.30         # > +30% above 200-DMA => Lane 3 0.0x run-up freeze (hold winners)
SIP_PULLBACK_DMA_DISTANCE_MAX = 0.15   # Lane 1 2.0x only while 0% <= distance <= +15%
SIP_PULLBACK_AER_MAX = 1.30            # Lane 1 2.5x requires AER < 1.30
SIP_LANE1_MULT = 2.50                  # Lane 1 double-down SIP multiplier
SIP_PULLBACK_WINNER_MARGIN = 0.10      # 2.0x requires XIRR > benchmark + 10%
SIP_BROKEN_TREND_AER = 0.85            # AER < 0.85 => Lane 4 broken-trend 0.0x warning
SIP_OVERALLOC_AER = 1.35               # AER >= 1.35 => Lane 3 over-allocation freeze (replaces fixed 8% cap)
MAX_SINGLE_STOCK_SIP = 35000.0         # per-stock monthly SIP cap (Rs, in 5k multiples)

# Smart New-Stock Incubation Pacing (invested_cost < INCUBATION_MAX_INVESTED)
INCUBATION_TIER_A_SIP = 20000.0        # fast-track starter: low vol + golden cross + ideal pullback
INCUBATION_TIER_B_SIP = 10000.0        # standard incubation pace
INCUBATION_TIER_C_SIP = 5000.0         # toe-hold / overheated / trend-not-confirmed guard
INCUBATION_GRADUATION_TARGET = 100000.0  # invested_cost at which incubation graduates
SIP_TIER_A_MAX_VOL = 0.28              # Tier A requires annual_volatility < 28%

DEFAULT_MONTHLY_SIP_BUDGET = 300000.0  # default total monthly SIP budget (Rs)

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
    invested: float = 0.0
    market_value: float | None = None
    xirr: float | None = None
    holding_start_date: date | None = None
    price_series: list[float] = field(default_factory=list)
    price_dates: list = field(default_factory=list)

    @property
    def cmp(self) -> float | None:
        """Current Market Price = latest Adjusted Close in the series."""
        return self.price_series[-1] if self.price_series else None


@dataclass
class PortfolioTotals:
    """Portfolio-level aggregates used for weighting."""

    total_invested: float = 0.0
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


def _dynamic_stop_loss_pct(annual_vol: float | None,
                           multiplier: float = STOP_LOSS_MULT) -> float | None:
    """``multiplier × volatility`` clamped to a [20%, 45%] band."""
    if annual_vol is None:
        return None
    return min(STOP_LOSS_CAP, max(STOP_LOSS_FLOOR, multiplier * annual_vol))


# --------------------------------------------------------------------------- #
# Core per-stock metric function
# --------------------------------------------------------------------------- #
def _incubation(stock: StockInput, as_of_date=None) -> bool:
    """True while a position is still in Incubation Mode (small OR young).

    A stock enters Incubation Mode **only** if BOTH hold:
      * fewer than 365 days have passed since its first buy, AND
      * either its remaining invested_cost is below ₹1,00,000 OR it has been held < 180 days.
    Once ``days_since_first_buy`` reaches 365 (held > 1 year), the stock can NEVER re-enter
    Incubation Mode — even if partial sells drive the remaining cost under ₹1L — so such old stub
    positions are evaluated for LAGGARD_EXIT instead of receiving fresh SIPs.
    """
    if stock.invested_cost is None:
        return False
    days = 0
    if as_of_date is not None and stock.holding_start_date is not None:
        try:
            days = (date.fromisoformat(as_of_date) - date.fromisoformat(stock.holding_start_date)).days
        except (TypeError, ValueError):
            return False
    return days < INCUBATION_MAX_AGE_DAYS and (stock.invested_cost < INCUBATION_MAX_INVESTED or days < 180)


def _peak_price_52w(price_series, price_dates, holding_start_date):
    """Highest Adjusted Close over the trailing 252 trading days on/after
    ``holding_start_date`` (falls back to the last 252 prices when dates absent)."""
    if not price_series:
        return None
    window = list(price_series[-VOL_WINDOW:])
    dates_window = list(price_dates[-VOL_WINDOW:]) if price_dates else []
    if dates_window and holding_start_date is not None and len(dates_window) == len(window):
        try:
            hsd = _to_date(holding_start_date)
            filtered = [p for d, p in zip(dates_window, window) if _to_date(d) >= hsd]
            if filtered:
                window = filtered
        except (TypeError, ValueError):
            pass
    return max(window) if window else None


def analyzeStockSignals(stock: StockInput, portfolioTotals: PortfolioTotals,
                        benchmarkXIRR: float | None,
                        stop_loss_mult: float = STOP_LOSS_MULT,
                        as_of_date=None,
                        excluded_tickers: set[str] = None) -> dict:
    """Compute the full metric bundle for one stock.

    Returns a dict with the 50/200-DMA, annualised volatility, dynamic
    stop-loss %, cost / market weight, AER and Efficiency Score, plus GTT
    protection fields (``incubation``, ``gtt_stop_loss_price``,
    ``gtt_buffer_pct``) and bookkeeping fields.

    ``stop_loss_mult`` overrides the volatility-to-stop-loss multiplier (Stage
    4B live control bar; defaults to :data:`STOP_LOSS_MULT`). ``as_of_date``
    enables the incubation-day count; when omitted only ``invested_cost`` is used.
    """
    series = [float(x) for x in (stock.price_series or []) if x is not None]
    n = len(series)

    excluded = False
    if excluded_tickers is not None:
        sid = str(stock.security_id).strip().upper()
        if sid in excluded_tickers:
            excluded = True

    dma_short = _sma(series, DMA_SHORT)
    dma_long = _sma(series, DMA_LONG)

    returns_window = series[-(VOL_WINDOW + 1):]
    annual_vol = _annualized_volatility(_daily_log_returns(returns_window))
    stop_loss = _dynamic_stop_loss_pct(annual_vol, stop_loss_mult)

    dma200_distance = None
    if dma_long is not None and dma_long > 0 and stock.cmp is not None:
        dma200_distance = (float(stock.cmp) - float(dma_long)) / float(dma_long)

    cost_weight = _safe_div(stock.invested_cost, portfolioTotals.total_invested_cost)
    market_weight = _safe_div(stock.market_value, portfolioTotals.total_market_value)
    aer = _safe_div(market_weight, cost_weight) if cost_weight else None
    efficiency = None
    if stock.xirr is not None and benchmarkXIRR is not None and aer is not None:
        efficiency = (float(stock.xirr) - float(benchmarkXIRR)) * aer

    # GTT protection: catastrophe breaker while incubating; cost-or-trailing-peak once graduated.
    incubation = _incubation(stock, as_of_date)
    cmp = stock.cmp
    gtt_price = None
    gtt_anchor_type = None
    gtt_anchor_price = None
    if stock.avg_buy_price and stock.avg_buy_price > 0:
        if incubation:
            gtt_price = stock.avg_buy_price * INCUBATION_GTT_MULT
            gtt_anchor_type = "Catastrophe Floor (40%)"
            gtt_anchor_price = stock.avg_buy_price
        else:
            # Default annual_volatility = 0.25 when price history is insufficient
            # (< 252 trading days) so GTT is never left unset and never falls to 0.50 x cost.
            _vol = annual_vol if (annual_vol is not None and n >= VOL_WINDOW) else 0.25
            stop_loss = _dynamic_stop_loss_pct(_vol, stop_loss_mult)
            cost_floor = stock.avg_buy_price * (1.0 - stop_loss)
            peak = _peak_price_52w(series, stock.price_dates, stock.holding_start_date)
            trailing_floor = 0.0
            if peak is not None and peak >= stock.avg_buy_price * TRAILING_PEAK_MULT:
                trailing_stop = min(TRAILING_STOP_CAP,
                                    max(TRAILING_STOP_FLOOR, TRAILING_STOP_MULT * _vol))
                trailing_floor = peak * (1.0 - trailing_stop)
            if trailing_floor > cost_floor:
                gtt_price = trailing_floor
                gtt_anchor_type = "Trailing Peak Floor"
                gtt_anchor_price = peak
            else:
                gtt_price = cost_floor
                gtt_anchor_type = "Cost Floor"
                gtt_anchor_price = stock.avg_buy_price
    gtt_buffer = ((cmp - gtt_price) / cmp) if (cmp and cmp > 0 and gtt_price is not None) else None

    return {
        "security_id": stock.security_id,
        "symbol": stock.symbol or stock.security_id,
        "cmp": cmp,
        "dma_50": dma_short,
        "dma_200": dma_long,
        "dma200_distance_pct": dma200_distance,
        "annual_volatility": annual_vol,
        "dynamic_stop_loss_pct": stop_loss,
        "cost_weight_pct": cost_weight,
        "market_weight_pct": market_weight,
        "aer": aer,
        "efficiency_score": efficiency,
        "incubation": incubation,
        "gtt_stop_loss_price": gtt_price,
        "gtt_buffer_pct": gtt_buffer,
        "gtt_anchor_type": gtt_anchor_type,
        "gtt_anchor_price": gtt_anchor_price,
        "trading_days": n,
        "insufficient_data": n < MIN_TRADING_DAYS,
        "excluded": excluded,
    }
    metrics["excluded"] = excluded


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
                    as_of_date=None, trim_aer: float = TRIM_AER,
                    min_market_value: float = TRIM_MIN_MARKET_VALUE,
                    excluded_tickers: set[str] = None) -> SignalResult:
    """Apply the four signal rules in strict priority order for one stock.

    ``trim_aer`` and ``min_market_value`` override the CONCENTRATION_TRIM
    thresholds (Stage 4B live control bar); defaults keep Stage 4A behaviour.
    """
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
    if (aer is not None and aer >= trim_aer
            and stock.market_value is not None and stock.market_value >= min_market_value
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
                       reason="CONCENTRATION_TRIM: AER>=" + f"{trim_aer:.2f}" + ", big winner, overweight")

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
                           frozen: bool = False,
                           max_stock_weight_pct: float = MAX_SINGLE_STOCK_WEIGHT_PCT,
                           excluded_tickers: set[str] = None) -> float:
    # Force zero SIP for excluded tickers so they don't consume budget or get buy signals.
    if excluded_tickers is not None and stock.security_id.strip().upper() in excluded_tickers:
        return 0.0
    """Return the monthly SIP multiplier (0.0 / 1.0 / 2.0) for a graduated stock.

    Four-lane ladder keyed off dma200_distance_pct = (CMP - 200-DMA) / 200-DMA:
    - Lane 3 (0.0x) Run-Up / Over-Allocated Freeze: distance > +30% OR AER >= 1.35
      (let winners run; warning_level stays False).
    - Lane 4 (0.0x) Broken Trend: CMP < 200-DMA OR AER < 0.85 (warning_level True).
    - Lane 1 (2.0x) Resting Winner: XIRR > benchmark + 10% AND 0% <= distance <= 15%
      AND AER < 1.25.
    - Lane 2 (1.0x) Cruising Stock: 0% <= distance <= 30% AND 0.85 <= AER < 1.35.
    Incubating stocks are locked at 1.0x by :func:`evaluate_signals` (not here).
    """
    if frozen:
        return 0.0
    cmp = metrics.get("cmp")
    aer = metrics.get("aer")
    dma_long = metrics.get("dma_200")
    xirr = stock.xirr
    if cmp is None or dma_long is None or aer is None or dma_long <= 0:
        return 0.0
    dma200_distance = (float(cmp) - float(dma_long)) / float(dma_long)

    # Lane 3 -- Run-Up / Over-Allocated Freeze (dma200 > +30% OR AER >= 1.35)
    if dma200_distance > SIP_RUN_UP_DMA_DISTANCE or aer >= SIP_OVERALLOC_AER:
        return 0.0
    # 0.0x -- Broken Trend (Warning Level): below 200-DMA or AER collapsed
    if dma200_distance < 0.0 or aer < SIP_BROKEN_TREND_AER:
        return 0.0
    # 2.0x -- Pullback-in-Uptrend Winner (proven winner consolidating near 200-DMA)
    if (xirr is not None and benchmarkXIRR is not None
            and xirr > benchmarkXIRR + SIP_PULLBACK_WINNER_MARGIN
            and 0.0 <= dma200_distance <= SIP_PULLBACK_DMA_DISTANCE_MAX
            and aer < SIP_PULLBACK_AER_MAX):
        return 2.5
    # 1.0x -- Steady Uptrend Accumulation
    if (0.0 <= dma200_distance <= SIP_RUN_UP_DMA_DISTANCE
            and SIP_BROKEN_TREND_AER <= aer < SIP_OVERALLOC_AER):
        return 1.0
    return 0.0



def incubation_starter_sip(metrics: dict) -> dict:
    # Starter monthly SIP for an incubating (< Rs 1,00,000) stock: Tier A/B/C.
    dma_short = metrics.get("dma_50")
    dma_long = metrics.get("dma_200")
    annual_vol = metrics.get("annual_volatility")
    dma200_dist = metrics.get("dma200_distance_pct")
    golden_cross = bool(dma_short is not None and dma_long is not None and dma_long > 0 and dma_short > dma_long)
    if dma200_dist is None:
        return {"sip": INCUBATION_TIER_C_SIP, "tier": "C", "is_golden_cross": golden_cross, "reason": "Insufficient price history - toe-hold Rs 5k"}
    if dma200_dist < 0.0:
        return {"sip": INCUBATION_TIER_C_SIP, "tier": "C", "is_golden_cross": golden_cross, "reason": "Below 200-DMA - trend not confirmed Rs 5k"}
    if dma200_dist > SIP_RUN_UP_DMA_DISTANCE:
        return {"sip": INCUBATION_TIER_C_SIP, "tier": "C", "is_golden_cross": golden_cross, "reason": "Overheated >30% above 200-DMA - toe-hold Rs 5k"}
    if (annual_vol is not None and annual_vol < SIP_TIER_A_MAX_VOL and golden_cross
            and dma200_dist <= SIP_PULLBACK_DMA_DISTANCE_MAX):
        return {"sip": INCUBATION_TIER_A_SIP, "tier": "A", "is_golden_cross": golden_cross, "reason": "Ideal 200-DMA pullback + golden cross - fast-track Rs 20k"}
    return {"sip": INCUBATION_TIER_B_SIP, "tier": "B", "is_golden_cross": golden_cross, "reason": "Standard incubation pace Rs 10k"}


def months_to_graduate(invested_cost, starter_sip) -> int | None:
    # Months to reach the Rs 1,00,000 graduation mark at the given monthly pace.
    try:
        sip = float(starter_sip)
    except (TypeError, ValueError):
        return None
    if sip <= 0:
        return None
    gap = max(0.0, INCUBATION_GRADUATION_TARGET - float(invested_cost or 0.0))
    return int(math.ceil(gap / sip))


# --------------------------------------------------------------------------- #
# Orchestrator: metrics + signals + SIP multipliers for a whole portfolio
# --------------------------------------------------------------------------- #
def evaluate_signals(stocks, portfolioTotals: PortfolioTotals,
                     benchmarkXIRR: float | None, as_of_date=None,
                     trim_aer: float = TRIM_AER, stop_loss_mult: float = STOP_LOSS_MULT,
                     min_market_value: float = TRIM_MIN_MARKET_VALUE,
                     max_stock_weight_pct: float = MAX_SINGLE_STOCK_WEIGHT_PCT,
                     excluded_tickers: set[str] = None) -> list[dict]:
    """Evaluate every stock and return enriched, priority-annotated rows."""

# ---------------------------------------------------------------------------
# Watchlist helpers
# ---------------------------------------------------------------------------

def resolve_watchlist_security(query: str, symbol_map=None):
    """Resolve a watchlist query to ISIN / Ticker / Company Name.

    Returns dict:
        {"isin": str or None,
         "ticker": str or None,
         "company_name": str or None,
         "resolved": bool}
    """
    q = str(query or "").strip().upper()
    if not q:
        return {"isin": None, "ticker": None, "company_name": "", "resolved": False}

    # Direct ISIN (12 chars starting with IN/INF)
    if (q.startswith("IN") and len(q) == 12) or q.startswith("INF"):
        isin = q
        resolved = True
    else:
        sm = symbol_map or {}
        for cand in (q, q + ".NS", q + ".BO"):
            if cand in sm:
                isin = sm[cand]
                resolved = True
                break
        else:
            core = q.split(".")[0] if "." in q else q
            for sym, isin_val in sm.items():
                if sym.split(".")[0] == core:
                    isin = isin_val
                    resolved = True
                    break
            else:
                isin = None
                resolved = False

    # Fallback via yfinance search
    if not resolved:
        try:
            import market_data.providers as p
            yf_mod = getattr(p, "yfinance", None)
            if yf_mod is not None:
                ticker_obj = yf_mod.Ticker(q)
                info = getattr(ticker_obj, "info", None)
                if info and (info.get("shortName") or info.get("symbol")):
                    isin = info.get("shortName") or info.get("symbol")
                    ticker = info.get("symbol") or ""
                    resolved = True
                elif info and info.get("exchange"):
                    isin = f"{q}.{info['exchange']}"
                    ticker = info.get("symbol") or ""
                    resolved = True
        except Exception:
            pass

    from app.shared import security_name
    company_name = security_name(isin, txs=None, holdings=None)

    return {
        "isin": isin or "",
        "ticker": ticker or "",
        "company_name": company_name or "",
        "resolved": resolved,
    }


def build_watchlist_stock_input(entry: dict, as_of_date=None) -> s4.StockInput:
    sid = (
        entry.get("isin") or entry.get("ticker") or entry.get("security_id") or ""
    ).strip().upper() or ""
    quantity = float(entry.get("invested", 0.0))
    avg_buy_price = float(entry.get("avg_buy_price", 100.0))
    invested = quantity
    mv = quantity * avg_buy_price if quantity > 0 else (quantity if avg_buy_price else 0.0)
    xirr = float(entry.get("xirr", 0.20)) if entry.get("xirr") is not None else 0.20
    holding_start_date = as_of_date or date(2026, 1, 1)
    price_series = []
    manual_cmp = entry.get("manual_cmp")
    if entry.get("imported_prices"):
        price_series = sorted(entry["imported_prices"], key=lambda row: row[0])[:200]
    elif manual_cmp is not None:
        price_series = [[as_of_date, float(manual_cmp)]]
    else:
        price_series = []
    return s4.StockInput(
        security_id=sid,
        symbol=sid,
        quantity=quantity,
        avg_buy_price=avg_buy_price,
        invested_cost=invested,
        market_value=mv,
        xirr=xirr,
        holding_start_date=holding_start_date,
        price_series=price_series,
        price_dates=[str(d) for d, _ in price_series],
    )


def import_watchlist_price_history(sid, csv_bytes):
    if not csv_bytes:
        return {"prices": [], "days": 0, "missing": True}
    try:
        text = csv_bytes.decode("utf-8").strip()
    except UnicodeDecodeError:
        text = csv_bytes.decode("latin-1").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() != ""]
    if not lines:
        return {"prices": [], "days": 0, "missing": True}
    dates = []
    prices = []
    for ln in lines:
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) >= 2:
            dates.append(str(parts[0]))
            prices.append(float(parts[1]))
    if dates:
        dates.sort()
        prices = [float(p) for _, p in zip(dates, prices)]
        n_days = len(dates)
        missing = n_days < MIN_PRICE_HISTORY_DAY if n_days else True
        return {"prices": list(zip(dates, prices)), "days": n_days, "missing": missing}
    return {"prices": [], "days": 0, "missing": True}


def compute_fixed_budget_sip_allocation(active_rows, watchlist_entries, total_deployable_budget=300000.0, max_single_stock_sip=35000.0, granularity=5000.0):
    """Allocate the fixed monthly cash-flow pool so total outflows never exceed ``total_deployable_budget``.

    Steps
    -----
    1. **Incubating SIPs** – every non-excluded stock whose `sip_multiplier == 1.0` (locked incubation) counts its starter SIP.
       Watchlist entries each get a starter SIP of ₹10,000 unless they have only a manual CMP (→ ₹5,000 tier-C toe-hold).
    2. **Remaining graduated budget** = ``max(0, total_deployable_budget - total_incubating_sip)``.
    3. Run existing ``compute_monthly_sip_allocation(active_rows, remaining_graduated_budget, ...)`` to distribute across graduated stocks.
    4. Unallocated remainder becomes liquid cash overflow.

    Returns
    -------
        dict with keys: rows, deployable_budget, total_allocated, parked_cash, total_units, all_frozen, carry_forward_cash.
    """
    # Step 1: incubating SIPs
    inc_sips = 0.0
    for entry in watchlist_entries:
        if entry.get("imported_prices"):
            sip = 10000.0  # sufficient price history → fast-track
        elif entry.get("manual_cmp") is not None:
            # Only manual CMP → toe-hold tier C (₹5k)
            sip = INCUBATION_TIER_C_SIP if len(entry.get("imported_prices", [])) < MIN_PRICE_HISTORY_DAY else 10000.0
        else:
            sip = 10000.0
        inc_sips += sip

    active_sips = sum(
        float(r.get("sip_multiplier") or 0) for r in active_rows if float(r.get("sip_multiplier") or 0) > 0
    )
    total_incubating_sip = inc_sips + active_sips

    remaining_graduated_budget = max(0.0, total_deployable_budget - total_incubating_sip)

    alloc = s4.compute_monthly_sip_allocation(active_rows, remaining_graduated_budget, harvested_cash=0.0, max_single_stock_sip=max_single_stock_sip)
    result = {
        "rows": alloc["rows"],
        "deployable_budget": alloc["deployable_budget"],
        "total_allocated": alloc["total_allocated"] + total_incubating_sip,
        "parked_cash": alloc["parked_cash"],
        "total_units": alloc["total_multiplier_units"],
        "all_frozen": alloc["all_frozen"],
        "carry_forward_cash": alloc["carry_forward_cash"],
    }
    return result


def rank_bottom_laggards(rows, n: int = 5) -> list[dict]:
    """Return the ``n`` holdings with the lowest Efficiency Score."""
    ranked = sorted(
        [r for r in rows or [] if r.get("efficiency_score") is not None],
        key=lambda r: r["efficiency_score"],
    )
    return ranked[:n]


def compute_monthly_sip_allocation(rows, total_monthly_budget,
                                   harvested_cash: float = 0.0,
                                   granularity: float = SIP_GRANULARITY,
                                   max_single_stock_sip: float | None = None) -> dict:
    # Exact-multiple SIP distribution. Base budget spreads across all eligible
    # holdings; harvested exit/trim cash is routed into Lane 1 (2x+) winners up to
    # their per-stock cap, with any excess parked as Dry Powder.
    rows = list(rows or [])
    budget_raw = float(total_monthly_budget or 0.0)
    harvest_raw = float(harvested_cash or 0.0)
    total_raw = budget_raw + harvest_raw
    if granularity and granularity > 0:
        deployable = math.floor(total_raw / granularity) * granularity
        base_budget = math.floor(budget_raw / granularity) * granularity
    else:
        deployable = total_raw
        base_budget = budget_raw
    carry_forward = total_raw - deployable

    total_units = sum(float(r.get("sip_multiplier") or 0.0) for r in rows
                      if float(r.get("sip_multiplier") or 0.0) > 0)

    mults: list[float] = []
    floored: list[float] = []
    remainders: list[float] = []
    for r in rows:
        mult = float(r.get("sip_multiplier") or 0.0)
        if total_units > 0 and mult > 0:
            raw = mult / total_units * base_budget
        else:
            raw = 0.0
        if granularity and granularity > 0:
            fl = math.floor(raw / granularity) * granularity
        else:
            fl = raw
        mults.append(mult)
        floored.append(fl)
        remainders.append(raw - fl)

    if max_single_stock_sip is not None and granularity and granularity > 0:
        cap = math.floor(max_single_stock_sip / granularity) * granularity
    else:
        cap = float("inf")
    for i in range(len(rows)):
        if floored[i] > cap:
            floored[i] = cap

    # Phase 1 -- spread leftover BASE budget by largest remainder, up to cap.
    remaining = base_budget - sum(floored)
    chunks = round(remaining / granularity) if granularity and granularity > 0 else 0
    eligible_idx = [i for i, m in enumerate(mults) if m > 0]
    order = sorted(eligible_idx, key=lambda i: (-remainders[i], -mults[i]))
    chunks = max(0, int(chunks))
    while chunks > 0 and order:
        progressed = False
        for idx in order:
            if chunks <= 0:
                break
            if floored[idx] + granularity <= cap:
                floored[idx] += granularity
                chunks -= 1
                progressed = True
        if not progressed:
            break

    # Phase 2 -- route harvested cash into active Lane 1 (2x+) winners up to cap.
    if granularity and granularity > 0:
        harvest_chunks = max(0, int(round((deployable - base_budget) / granularity)))
    else:
        harvest_chunks = 0
    lane1_idx = [i for i, m in enumerate(mults) if m >= SIP_LANE1_MULT - 1e-9]
    while harvest_chunks > 0 and lane1_idx:
        progressed = False
        for idx in lane1_idx:
            if harvest_chunks <= 0:
                break
            if floored[idx] + granularity <= cap:
                floored[idx] += granularity
                harvest_chunks -= 1
                progressed = True
        if not progressed:
            break

    total_allocated = sum(floored)
    parked_cash = deployable - total_allocated

    out = []
    for i, r in enumerate(rows):
        enriched = dict(r)
        enriched["recommended_sip_rupees"] = floored[i]
        cmp_ = r.get("cmp") or 0.0
        enriched["est_shares"] = math.floor(floored[i] / cmp_) if cmp_ and cmp_ > 0 else 0
        out.append(enriched)
    return {
        "rows": out,
        "deployable_budget": deployable,
        "total_allocated": total_allocated,
        "parked_cash": parked_cash,
        "total_multiplier_units": total_units,
        "all_frozen": total_units <= 0,
        "carry_forward_cash": carry_forward,
    }


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
# FIFO lot-level detail + India-equity tax preview (LTCG / STCG)
# --------------------------------------------------------------------------- #
def fifo_lots(transactions, security_id, as_of_date, opening_positions=None):
    """Open FIFO lots for the currently-held shares.

    Returns ``[{"qty", "unit_price", "buy_date"}]`` oldest-first, so callers can
    inspect holding periods (used for the tax preview).
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
        events.append((od, "BUY", oq, 0.0))

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
    lots: list[list] = []  # [qty, unit_price, buy_date]
    for dd, kind, q, amt in events:
        if kind == "BUY":
            unit = (amt / q) if q > 0 and amt else 0.0
            lots.append([q, unit, dd])
        else:
            remaining = q
            while remaining > 1e-9 and lots:
                take = min(lots[0][0], remaining)
                lots[0][0] -= take
                remaining -= take
                if lots[0][0] <= 1e-9:
                    lots.pop(0)
    return [{"qty": l[0], "unit_price": l[1], "buy_date": l[2]} for l in lots if l[0] > 1e-9]


def fifo_tax_preview(transactions, security_id, shares_to_sell, sale_price, as_of_date,
                     opening_positions=None, ltcg_rate=0.125, stcg_rate=0.20,
                     ltcg_exemption=125000.0, ltcg_min_days=365):
    """FIFO tax preview for selling ``shares_to_sell`` at ``sale_price``.

    India equity rules: lots held > 365 days are **LTCG** (12.5% above a
    ₹1.25L annual exemption); lots held ≤ 365 days are **STCG** (20%). Returns
    per-bucket shares, realized gain, estimated tax and net post-tax cash. The
    ₹1.25L exemption is annual/portfolio-wide; applied here per-stock as an
    illustration.
    """
    ads = _to_date(as_of_date)
    lots = fifo_lots(transactions, security_id, ads, opening_positions)
    want = max(0.0, float(shares_to_sell or 0.0))
    price = float(sale_price or 0.0)

    ltcg_sh = stcg_sh = 0.0
    ltcg_gain = stcg_gain = 0.0
    remaining = want
    for lot in lots:
        if remaining <= 1e-9:
            break
        take = min(lot["qty"], remaining)
        remaining -= take
        gain = (price - lot["unit_price"]) * take
        held_days = (ads - lot["buy_date"]).days
        if held_days > ltcg_min_days:
            ltcg_sh += take
            ltcg_gain += gain
        else:
            stcg_sh += take
            stcg_gain += gain

    ltcg_tax = max(0.0, ltcg_gain - ltcg_exemption) * ltcg_rate
    stcg_tax = max(0.0, stcg_gain) * stcg_rate
    total_tax = ltcg_tax + stcg_tax
    gross = want * price
    return {
        "shares_sold": want,
        "sale_price": price,
        "ltcg_shares": ltcg_sh,
        "stcg_shares": stcg_sh,
        "ltcg_gain": ltcg_gain,
        "stcg_gain": stcg_gain,
        "realized_gain": ltcg_gain + stcg_gain,
        "estimated_tax": total_tax,
        "gross_proceeds": gross,
        "net_cash_unlocked": gross - total_tax,
    }


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


def _default_adjusted_close_pairs(security_id, as_of_date, db_path=None):
    """Ascending ``(trade_date, adjusted_close)`` pairs (``<= as_of_date``)."""
    db = (db_path or os.environ.get("ICICI_MARKET_DB")
          or os.environ.get("DEFAULT_MARKET_DB") or "data/market_data.sqlite3")
    sid = str(security_id).strip().upper()
    cutoff = str(_to_date(as_of_date))[:10]
    try:
        con = sqlite3.connect(db)
        rows = con.execute(
            "SELECT trade_date, COALESCE(adjusted_close, close_price) FROM stock_prices "
            "WHERE UPPER(security_id)=? AND trade_date<=? ORDER BY trade_date",
            (sid, cutoff),
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    return [(str(r[0])[:10], float(r[1])) for r in rows if r[1] is not None]


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

    price_dates = []
    if series:
        try:
            pairs = _default_adjusted_close_pairs(sid, ad)
            if len(pairs) == len(series):
                price_dates = [d for d, _ in pairs]
        except Exception:  # noqa: BLE001
            price_dates = []

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
        price_dates=price_dates,
    )


def build_portfolio_totals(stocks, excluded_tickers: set[str] = None) -> PortfolioTotals:
    """Sum invested cost and market value across :class:`StockInput` objects.

    Excluded tickers (set[str]) are subtracted from both total_invested_cost and total_market_value so
    AER (Market Weight / Cost Weight) denominators ignore non-equity holdings like NCDs.
    """
    total_cost = 0.0
    total_mv = 0.0
    for s in stocks or []:
        sid = str(s.security_id).strip().upper() if s.security_id else ""
        if excluded_tickers is not None and (sid in excluded_tickers or s.invested_cost is None):
            continue
        total_cost += float(s.invested_cost or 0.0)
        total_mv += float(s.market_value or 0.0)
    return PortfolioTotals(total_invested_cost=total_cost, total_market_value=total_mv)
