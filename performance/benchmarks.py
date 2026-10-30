"""Benchmark comparison engine (Stage 08)."""
from __future__ import annotations

from datetime import date, datetime

from performance.xirr import calculate_portfolio_twr, calculate_portfolio_xirr
from src.xirr import compute_xirr

TOL = 1e-9

def _to_date(v):
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                return datetime.strptime(v.strip(), fmt).date()
            except ValueError:
                continue
    raise ValueError("bad date")


def simulate_benchmark_investment(cashflows, index_lookup, start_date, end_date):
    sd = _to_date(start_date)
    ed = _to_date(end_date)
    flows = sorted(cashflows or [], key=lambda x: _to_date(x[0]))
    units = 0.0
    records = []
    for d, amt in flows:
        dd = _to_date(d)
        if dd < sd or dd > ed:
            continue
        if callable(index_lookup):
            lvl = index_lookup(dd)
        else:
            lvl = index_lookup.get(dd)
        if lvl is None:
            continue
        lvl = float(lvl)
        if amt < 0:
            units += (-amt) / lvl
            records.append((dd, amt, lvl, (-amt)/lvl))
        elif amt > 0:
            # redeem units to raise amt
            units -= amt / lvl
            records.append((dd, amt, lvl, -amt/lvl))
    # terminal level
    if callable(index_lookup):
        tl = index_lookup(ed)
    else:
        tl = index_lookup.get(ed)
    terminal_value = units * float(tl) if tl is not None else None
    return {"units": units, "terminal_value": terminal_value, "records": records}


def calculate_benchmark_xirr(cashflows, index_lookup, start_date, end_date):
    sim = simulate_benchmark_investment(cashflows, index_lookup, start_date, end_date)
    tv = sim.get('terminal_value')
    if tv is None:
        return {'xirr': None, 'cashflows': [], 'reason': 'missing_index'}
    orig = []
    for c in cashflows or []:
        dd = _to_date(c[0])
        sd = _to_date(start_date)
        ed = _to_date(end_date)
        if sd <= dd <= ed:
            orig.append((dd, float(c[1])))
    edd = _to_date(end_date)
    orig.append((edd, float(tv)))
    x = compute_xirr(orig)
    return {'xirr': x, 'cashflows': orig, 'units': sim['units'], 'terminal_value': tv}


def _periods(start, end, frequency):
    from datetime import date
    s = _to_date(start)
    e = _to_date(end)
    out = []
    if frequency == "calendar":
        for y in range(s.year, e.year + 1):
            ys = date(y, 1, 1)
            ye = date(y, 12, 31)
            ps = max(ys, s)
            pe = min(ye, e)
            partial = not (ps == ys and pe == ye)
            out.append((str(y), ps, pe, partial))
    elif frequency == "financial":
        y = s.year if s.month >= 4 else s.year - 1
        while True:
            ys = date(y, 4, 1)
            ye = date(y + 1, 3, 31)
            ps = max(ys, s)
            pe = min(ye, e)
            if ps > e:
                break
            if pe >= ps:
                partial = not (ps == ys and pe == ye)
                out.append((f"FY{y}-{str(y+1)[-2:]}", ps, pe, partial))
            y += 1
            if ys > e:
                break
    elif frequency == "ytd":
        ys = date(s.year, 1, 1)
        out.append(("YTD", max(ys, s), e, True))
    else:
        out.append(("Custom", s, e, False))
    out = [p for p in out if p[1] <= p[2]]
    return out


def calculate_annual_returns(transactions, actions=None, dividends=None, price_lookup=None, index_lookup_50=None, index_lookup_500=None, start_date=None, end_date=None, frequency="calendar"):
    from datetime import date as _date
    s = _to_date(start_date) if start_date else None
    e = _to_date(end_date) if end_date else None
    if s is None:
        ds = [_to_date(t.get("trade_date") or t.get("settlement_date") or t.get("date")) for t in (transactions or [])]
        s = min(ds) if ds else _date.today()
    if e is None:
        ds = [_to_date(t.get("trade_date") or t.get("settlement_date") or t.get("date")) for t in (transactions or [])]
        e = max(ds) if ds else _date.today()
    periods = _periods(s, e, frequency)
    out = []
    for label, ps, pe, partial in periods:
        twr = calculate_portfolio_twr(transactions, actions, dividends, price_lookup, ps, pe)
        px = calculate_portfolio_xirr(transactions, actions, dividends, price_lookup, ps, pe)
        def tri_ret(lookup, _ps=ps, _pe=pe):
            if lookup is None:
                return None
            a = lookup(_ps) if callable(lookup) else lookup.get(_ps)
            b = lookup(_pe) if callable(lookup) else lookup.get(_pe)
            if a is None or b is None:
                return None
            try:
                a = float(a); b = float(b)
            except Exception:  # noqa: BLE001
                return None
            if a == 0:
                return None
            return b / a - 1.0
        n50 = tri_ret(index_lookup_50)
        n500 = tri_ret(index_lookup_500)
        b_xirr = None
        try:
            pcfs = px.get("cashflows", []) if isinstance(px, dict) else []
            nonterm = [ (d, a) for (d, a, lbl) in pcfs if lbl != "terminal_value" ]
            bres = calculate_benchmark_xirr(nonterm, index_lookup_50, ps, pe)
            b_xirr = bres.get("xirr")
        except Exception:  # noqa: BLE001
            b_xirr = None
        excess = None
        if isinstance(px, dict) and px.get("xirr") is not None and n50 is not None:
            excess = px["xirr"] - n50
        out.append({
            "period": label,
            "start": ps,
            "end": pe,
            "partial": partial,
            "portfolio_twr": twr,
            "portfolio_xirr": px.get("xirr") if isinstance(px, dict) else None,
            "nifty50_tri_return": n50,
            "nifty500_tri_return": n500,
            "benchmark_matched_xirr": b_xirr,
            "excess_return": excess,
            "annual_economic_gain_inr": None,
        })
    return out


def compare_portfolio_benchmarks(transactions, actions=None, dividends=None, price_lookup=None, index_lookup_50=None, index_lookup_500=None, start_date=None, end_date=None):
    tw = calculate_portfolio_twr(transactions, actions, dividends, price_lookup, start_date, end_date)
    px = calculate_portfolio_xirr(transactions, actions, dividends, price_lookup, start_date, end_date)
    def tri(lookup, d):
        if lookup is None:
            return None
        v = lookup(d) if callable(lookup) else lookup.get(d)
        return float(v) if v is not None else None
    sd = _to_date(start_date); ed = _to_date(end_date)
    a50 = tri(index_lookup_50, sd); b50 = tri(index_lookup_50, ed)
    n50 = (b50 / a50 - 1.0) if (a50 and b50) else None
    a500 = tri(index_lookup_500, sd); b500 = tri(index_lookup_500, ed)
    n500 = (b500 / a500 - 1.0) if (a500 and b500) else None
    b_xirr = None
    try:
        pcfs = px.get("cashflows", []) if isinstance(px, dict) else []
        nonterm = [ (d, a) for (d, a, lbl) in pcfs if lbl != "terminal_value" ]
        bres = calculate_benchmark_xirr(nonterm, index_lookup_50, start_date, end_date)
        b_xirr = bres.get("xirr")
    except Exception:  # noqa: BLE001
        b_xirr = None
    return {
        "methodology_time_weighted": {
            "portfolio_twr": tw,
            "nifty50_tri_return": n50,
            "nifty500_tri_return": n500,
            "excess_vs_nifty50": (tw - n50) if (tw is not None and n50 is not None) else None,
        },
        "methodology_cash_flow_matched": {
            "portfolio_xirr": px.get("xirr") if isinstance(px, dict) else None,
            "benchmark_xirr": b_xirr,
            "excess": (px.get("xirr") - b_xirr) if (isinstance(px, dict) and px.get("xirr") is not None and b_xirr is not None) else None,
        },
    }
