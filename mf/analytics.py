"""Deterministic MF performance & risk analytics (Stage 7).

All functions are pure and deterministic: given the same NAV series they always
produce identical results (no randomness). NAV series are sequences of
``(date_str, nav_float)``. These never touch accounting/ledger logic.
"""
from __future__ import annotations

import math
from datetime import date
from statistics import pstdev

TRADING_DAYS = 252


def _to_date(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def to_series(records) -> list[tuple[date, float]]:
    """Normalise raw (date, nav) records into a sorted, de-duplicated float series."""
    out: dict[date, float] = {}
    for d, nav in records:
        dt = _to_date(d)
        try:
            nv = float(nav)
        except (TypeError, ValueError):
            continue
        if dt is not None and nv > 0:
            out[dt] = nv
    return sorted(out.items())


def periodic_returns(series: list[tuple[date, float]]) -> list[float]:
    """Simple period-over-period returns from a NAV series."""
    rets: list[float] = []
    for i in range(1, len(series)):
        prev = series[i - 1][1]
        cur = series[i][1]
        if prev > 0:
            rets.append(cur / prev - 1.0)
    return rets


def trailing_return(series: list[tuple[date, float]], years: float) -> float | None:
    """Point-to-point annualised return over the last ``years`` (CAGR)."""
    if len(series) < 2:
        return None
    end_d, end_v = series[-1]
    target = date(end_d.year - int(years), end_d.month, min(end_d.day, 28))
    start_pair = None
    for d, v in series:
        if d <= target:
            start_pair = (d, v)
        else:
            break
    if start_pair is None:
        start_pair = series[0]
    start_d, start_v = start_pair
    days = (end_d - start_d).days
    if days <= 0 or start_v <= 0:
        return None
    if days < years * 365 * 0.8:
        return None
    total = end_v / start_v - 1.0
    return (1.0 + total) ** (365.0 / days) - 1.0


def trailing_returns(series: list[tuple[date, float]], years_list=(1, 3, 5)) -> dict[str, float | None]:
    """Annualised trailing returns for each horizon in ``years_list``."""
    return {f"{y}y": trailing_return(series, y) for y in years_list}


def annualized_volatility(returns: list[float], periods_per_year: int = TRADING_DAYS) -> float | None:
    """Annualised standard deviation of periodic returns."""
    if len(returns) < 2:
        return None
    return pstdev(returns) * math.sqrt(periods_per_year)


def max_drawdown(series: list[tuple[date, float]]) -> dict | None:
    """Maximum peak-to-trough drawdown (as a negative fraction) with dates."""
    if len(series) < 2:
        return None
    max_dd = 0.0
    dd_peak = series[0][0]
    dd_trough = series[0][0]
    cur_peak_v, cur_peak_d = series[0][1], series[0][0]
    for d, v in series[1:]:
        if v > cur_peak_v:
            cur_peak_v, cur_peak_d = v, d
        dd = v / cur_peak_v - 1.0 if cur_peak_v > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
            dd_peak, dd_trough = cur_peak_d, d
    return {
        "max_drawdown": round(max_dd, 6),
        "peak_date": dd_peak.isoformat(),
        "trough_date": dd_trough.isoformat(),
    }


def sharpe_ratio(returns: list[float], risk_free_annual: float = 0.06,
                 periods_per_year: int = TRADING_DAYS) -> float | None:
    """Annualised Sharpe ratio (excess mean return / volatility)."""
    if len(returns) < 2:
        return None
    vol = pstdev(returns)
    if vol == 0:
        return None
    rf_period = risk_free_annual / periods_per_year
    mean_excess = sum(r - rf_period for r in returns) / len(returns)
    return (mean_excess / vol) * math.sqrt(periods_per_year)


def sortino_ratio(returns: list[float], risk_free_annual: float = 0.06,
                  periods_per_year: int = TRADING_DAYS) -> float | None:
    """Annualised Sortino ratio using downside deviation."""
    if len(returns) < 2:
        return None
    rf_period = risk_free_annual / periods_per_year
    downside = [r - rf_period for r in returns if r - rf_period < 0]
    if not downside:
        return None
    dd = math.sqrt(sum(x * x for x in downside) / len(downside))
    if dd == 0:
        return None
    mean_excess = sum(r - rf_period for r in returns) / len(returns)
    return (mean_excess / dd) * math.sqrt(periods_per_year)


def align_returns(fund: list[tuple[date, float]], bench: list[tuple[date, float]]) -> tuple[list[float], list[float]]:
    """Align two NAV series on common dates and return their periodic returns."""
    fd = dict(fund)
    bd = dict(bench)
    common = sorted(set(fd) & set(bd))
    if len(common) < 2:
        return [], []
    f_rets, b_rets = [], []
    for i in range(1, len(common)):
        p, c = common[i - 1], common[i]
        if fd[p] > 0 and bd[p] > 0:
            f_rets.append(fd[c] / fd[p] - 1.0)
            b_rets.append(bd[c] / bd[p] - 1.0)
    return f_rets, b_rets


def downside_capture(fund_returns: list[float], bench_returns: list[float]) -> float | None:
    """Fund's average return in benchmark-down periods ÷ benchmark's average down return."""
    if not fund_returns or len(fund_returns) != len(bench_returns):
        return None
    pairs = list(zip(fund_returns, bench_returns))
    b_down = [b for f, b in pairs if b < 0]
    if not b_down:
        return None
    b_avg = sum(b_down) / len(b_down)
    if b_avg == 0:
        return None
    f_down = [f for f, b in pairs if b < 0]
    return (sum(f_down) / len(f_down)) / b_avg


def tracking_error(fund_returns: list[float], bench_returns: list[float],
                   periods_per_year: int = TRADING_DAYS) -> float | None:
    """Annualised tracking error (std of active returns)."""
    if not fund_returns or len(fund_returns) != len(bench_returns) or len(fund_returns) < 2:
        return None
    active = [f - b for f, b in zip(fund_returns, bench_returns)]
    return pstdev(active) * math.sqrt(periods_per_year)


def rolling_returns(series: list[tuple[date, float]], window_years: float = 1.0) -> list[float]:
    """Annualised rolling returns over sliding windows of ``window_years``."""
    out: list[float] = []
    if len(series) < 2:
        return out
    window_days = int(window_years * 365)
    j = 0
    for i in range(len(series)):
        while (series[i][0] - series[j][0]).days > window_days:
            j += 1
        if i > j and (series[i][0] - series[j][0]).days >= window_days * 0.9:
            v0 = series[j][1]
            if v0 > 0:
                days = (series[i][0] - series[j][0]).days
                out.append((series[i][1] / v0) ** (365.0 / days) - 1.0)
    return out


def performance_consistency(fund: list[tuple[date, float]], bench: list[tuple[date, float]],
                            window_years: float = 1.0) -> float | None:
    """Fraction of rolling windows where the fund beat the benchmark (0..1)."""
    fr = rolling_returns(fund, window_years)
    br = rolling_returns(bench, window_years)
    n = min(len(fr), len(br))
    if n == 0:
        return None
    return sum(1 for i in range(n) if fr[i] >= br[i]) / n


def summarize_fund(nav_records, benchmark_records=None, risk_free_annual: float = 0.06) -> dict:
    """Compute a full deterministic analytics summary for one scheme."""
    series = to_series(nav_records)
    rets = periodic_returns(series)
    summary: dict = {
        "observations": len(series),
        "start_date": series[0][0].isoformat() if series else None,
        "end_date": series[-1][0].isoformat() if series else None,
        "trailing_returns": trailing_returns(series),
        "annualized_volatility": annualized_volatility(rets),
        "max_drawdown": max_drawdown(series),
        "sharpe_ratio": sharpe_ratio(rets, risk_free_annual),
        "sortino_ratio": sortino_ratio(rets, risk_free_annual),
    }
    if benchmark_records:
        bseries = to_series(benchmark_records)
        f_rets, b_rets = align_returns(series, bseries)
        summary["downside_capture"] = downside_capture(f_rets, b_rets)
        summary["tracking_error"] = tracking_error(f_rets, b_rets)
        summary["performance_consistency_1y"] = performance_consistency(series, bseries, 1.0)
        bench_trailing = trailing_returns(bseries)
        summary["benchmark_trailing_returns"] = bench_trailing
        tr = summary["trailing_returns"].get("3y")
        btr = bench_trailing.get("3y")
        summary["excess_3y"] = (tr - btr) if (tr is not None and btr is not None) else None
    return summary
