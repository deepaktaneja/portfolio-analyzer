"""Point-in-time signal backtester (Stage 8).

Evaluates previously-generated signals against *subsequent* prices only (no
look-ahead in evaluation). Signal generation itself must use only data known at
the signal date — helpers here enforce that boundary. Deterministic.
"""
from __future__ import annotations

from datetime import date, timedelta

HORIZONS = {"1m": 30, "3m": 90, "6m": 180, "12m": 365}
BUY_HOLD_COST_PCT = 0.0015  # illustrative round-trip cost assumption


def _to_date(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _series_map(records) -> list[tuple[date, float]]:
    out = []
    for d, p in records or []:
        dt = _to_date(d)
        try:
            pv = float(p)
        except (TypeError, ValueError):
            continue
        if dt and pv > 0:
            out.append((dt, pv))
    return sorted(out)


def price_as_of(series: list[tuple[date, float]], as_of: date) -> float | None:
    """Latest price on/before ``as_of`` (point-in-time; never uses future bars)."""
    val = None
    for d, p in series:
        if d <= as_of:
            val = p
        else:
            break
    return val


def forward_return(series: list[tuple[date, float]], from_date: date, days: int) -> float | None:
    """Return from ``from_date`` price to the price ~``days`` later (evaluation only).

    Uses the nearest observation **on/after** the horizon (standard forward-return
    convention); falls back to the last observation on/before it. This avoids
    reporting a spurious 0% when the horizon falls just short of the next bar.
    (Stage 11 audit fix.)
    """
    start = None
    for d, p in series:
        if d <= from_date:
            start = p
        else:
            break
    if start is None or start <= 0:
        return None
    target = from_date + timedelta(days=days)
    end = None
    end_date = None
    # Preferred: first observation on/after the horizon (nearest trading day ahead).
    for d, p in series:
        if d >= target and d > from_date:
            end, end_date = p, d
            break
    if end is None:
        # Fallback: last observation on/before the horizon.
        for d, p in series:
            if d <= target:
                end, end_date = p, d
            else:
                break
    if end is None or end <= 0 or (end_date is not None and end_date <= from_date):
        return None
    return end / start - 1.0


def filter_signals_until(events: list[dict], as_of) -> list[dict]:
    """Keep only signals dated on/before ``as_of`` (point-in-time guard)."""
    cut = _to_date(as_of)
    if cut is None:
        return list(events)
    out = []
    for e in events:
        d = _to_date(e.get("date"))
        if d is not None and d <= cut:
            out.append(e)
    return out


def evaluate_signals(price_data: dict[str, list], signals: list[dict],
                     false_positive_pct: float = 0.10, missed_upside_pct: float = 0.20) -> dict:
    """Evaluate dated signal events against subsequent price moves.

    ``signals`` items: {"symbol", "date", "action"} where action ∈
    {TRIM_REVIEW, EXIT_REVIEW, REPLACEMENT_REVIEW, HOLD, MONITOR}.
    """
    series_by_symbol = {s: _series_map(records) for s, records in (price_data or {}).items()}
    details: list[dict] = []
    unvalidatable: list[dict] = []
    sell_actions = {"TRIM_REVIEW", "EXIT_REVIEW", "REPLACEMENT_REVIEW"}

    for ev in signals or []:
        sym = str(ev.get("symbol") or ev.get("security_id") or "").strip().upper()
        d = _to_date(ev.get("date"))
        series = series_by_symbol.get(sym, [])
        if d is None or not series:
            unvalidatable.append({"symbol": sym, "date": str(ev.get("date")), "reason": "missing price series"})
            continue
        row = {"symbol": sym, "date": d.isoformat(), "action": ev.get("action")}
        missing = False
        for label, days in HORIZONS.items():
            r = forward_return(series, d, days)
            row[f"return_{label}"] = round(r, 4) if r is not None else None
            if r is None:
                missing = True
        row["class"] = "sell" if ev.get("action") in sell_actions else "hold"
        row["validatable"] = not missing
        details.append(row)
        if missing:
            unvalidatable.append({"symbol": sym, "date": d.isoformat(), "reason": "incomplete forward prices"})

    # Aggregate signal-performance metrics over sell-type signals
    sell_rows = [r for r in details if r["class"] == "sell" and r.get("return_3m") is not None]
    hit_rate = fp_rate = missed_rate = None
    signal_value = None
    if sell_rows:
        hits = sum(1 for r in sell_rows if r["return_3m"] < 0)
        hit_rate = round(hits / len(sell_rows), 4)
        fp = [r for r in sell_rows if r.get("return_6m") is not None]
        if fp:
            fp_rate = round(sum(1 for r in fp if r["return_6m"] > false_positive_pct) / len(fp), 4)
            missed_rate = round(sum(1 for r in fp if r["return_6m"] > missed_upside_pct) / len(fp), 4)
        # Transparency: value proxy = avoided 3m loss (or missed gain) minus cost, per sell signal
        vals = [(-r["return_3m"] - BUY_HOLD_COST_PCT) for r in sell_rows]
        signal_value = round(sum(vals) / len(vals), 4)

    # Buy-and-hold baseline (equal-weight first->last across symbols with data)
    bh_returns = []
    for series in series_by_symbol.values():
        if len(series) >= 2:
            bh_returns.append(series[-1][1] / series[0][1] - 1.0)
    buy_hold = round(sum(bh_returns) / len(bh_returns), 4) if bh_returns else None

    return {
        "signals_evaluated": len(details),
        "signals_validatable": sum(1 for r in details if r.get("validatable")),
        "unvalidatable": unvalidatable,
        "hit_rate": hit_rate,
        "false_positive_rate": fp_rate,
        "missed_upside_rate": missed_rate,
        "signal_value_proxy": signal_value,
        "buy_hold_return": buy_hold,
        "cost_assumption_pct": BUY_HOLD_COST_PCT,
        "details": details,
    }


def run_backtest(price_data: dict[str, list], signals: list[dict], **kwargs) -> dict:
    """Convenience wrapper around :func:`evaluate_signals`."""
    return evaluate_signals(price_data, signals, **kwargs)
