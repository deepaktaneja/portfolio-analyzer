"""Direct Equity Page 2 analytics: local math + benchmark indexing."""
from __future__ import annotations

from datetime import date, datetime, timedelta


def _to_date(v):
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()
    raise ValueError("bad date")

def _tx_date(t):
    return t.get("trade_date") or t.get("settlement_date") or t.get("date")

def _tx_side(t):
    return str(t.get("buy_sell") or t.get("side") or "").strip().upper()

def _net_flow(txs, start, end):
    sd = str(_to_date(start))[:10]
    ed = str(_to_date(end))[:10]
    total = 0.0
    for t in txs or []:
        try:
            d = str(_to_date(_tx_date(t)))[:10]
        except Exception:  # noqa: BLE001, S112
            continue
        if not (sd < d <= ed):
            continue
        try:
            amt = float(t.get("net_amount") or 0.0)
        except (TypeError, ValueError):
            amt = 0.0
        s = _tx_side(t)
        if s in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            total += amt
        elif s == "SELL":
            total -= amt
    return total

def portfolio_snapshot(txs, price_lookup, as_of_date, opening_positions=None):
    from performance.xirr import calculate_portfolio_snapshot
    return calculate_portfolio_snapshot(txs, price_lookup=price_lookup, as_of_date=as_of_date, opening_positions=opening_positions)

def portfolio_xirr(txs, actions, dividends, price_lookup, start_date, end_date, opening_positions=None):
    from performance.xirr import calculate_portfolio_xirr
    res = calculate_portfolio_xirr(txs, actions=actions, dividends=dividends, price_lookup=price_lookup, start_date=start_date, end_date=end_date, opening_positions=opening_positions)
    return res.get("xirr") if isinstance(res, dict) else None

def normalize(values, base=100.0):
    out = []
    fv = None
    for v in values:
        if fv is None and v is not None and v > 0:
            fv = v
        out.append(None if (fv is None or v is None) else round(base * v / fv, 4))
    return out

def _sample_dates(start, end, max_points=48):
    sd, ed = _to_date(start), _to_date(end)
    days = (ed - sd).days
    if days <= 0:
        return []
    step = max(1, days // max_points)
    out = []
    d = sd
    while d < ed:
        out.append(d)
        d = d + timedelta(days=step)
    out.append(ed)
    return out

def twr_index_series(txs, price_lookup, start_date, end_date, opening_positions=None, base=100.0):
    """Portfolio market value indexed to ``base`` at the first positive value.
    Direct value growth (contribution-inclusive), rebased to base. Sample
    dates land on calendar dates but the snapshot engine pulls the last
    available price, so values are stable across non-trading days.
    """
    dates = _sample_dates(start_date, end_date)
    if not dates:
        return []
    values = []
    for d in dates:
        try:
            snap = portfolio_snapshot(txs, price_lookup, d, opening_positions)
            values.append(float(snap.get("portfolio_value") or 0.0))
        except Exception:  # noqa: BLE001
            values.append(0.0)
    return [{"date": str(d), "nav": n} for d, n in zip(dates, normalize(values, base))]


def benchmark_index_series(index_lookup, start_date, end_date, base=100.0):
    dates = _sample_dates(start_date, end_date)
    levels = []
    for d in dates:
        try:
            lvl = index_lookup(d)
        except Exception:  # noqa: BLE001
            lvl = None
        levels.append(float(lvl) if lvl is not None else None)
    return [{"date": str(d), "index": n} for d, n in zip(dates, normalize(levels, base))]

def alpha(portfolio_return, benchmark_return):
    if portfolio_return is None or benchmark_return is None:
        return None
    return float(portfolio_return) - float(benchmark_return)

def total_dividends(dividend_flows, start_date=None, end_date=None, use="net"):
    sd = str(_to_date(start_date))[:10] if start_date else None
    ed = str(_to_date(end_date))[:10] if end_date else None
    total = 0.0
    for d in dividend_flows or []:
        dt = str(d.get("payment_date") or d.get("ex_date") or d.get("date") or "")[:10]
        if not dt:
            continue
        if sd and dt < sd:
            continue
        if ed and dt > ed:
            continue
        amt = d.get(use) if use in ("gross", "net") else None
        if amt is None:
            amt = d.get("gross") or d.get("amount") or 0.0
        try:
            total += float(amt)
        except (TypeError, ValueError):
            continue
    return total

def realized_pnl(txs, actions=None, start_date=None, end_date=None):
    from accounting.fifo import calculate_realized_gains
    try:
        gains = calculate_realized_gains(txs, actions=actions, start_date=start_date, end_date=end_date)
    except Exception:  # noqa: BLE001
        return 0.0
    total = 0.0
    for g in gains or []:
        try:
            total += float(g.get("gain") or 0.0)
        except (TypeError, ValueError):
            continue
    return total


def stock_flows(transactions, dividends, security_id, start_date=None, end_date=None, terminal_value=None, terminal_date=None):
    sid = str(security_id or "").strip().upper()
    sd = _to_date(start_date) if start_date else None
    ed = _to_date(end_date) if end_date else None
    flows = []
    for t in transactions or []:
        if str(t.get("isin") or t.get("security") or t.get("symbol") or "").strip().upper() != sid:
            continue
        try:
            dd = _to_date(_tx_date(t))
        except Exception:  # noqa: BLE001, S112
            continue
        if (sd and dd < sd) or (ed and dd > ed):
            continue
        try:
            amt = float(t.get("net_amount") or 0.0)
        except (TypeError, ValueError):
            amt = 0.0
        side = _tx_side(t)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            flows.append((dd, -abs(amt), "buy"))
        elif side == "SELL":
            flows.append((dd, abs(amt), "sell"))
    for d in dividends or []:
        if str(d.get("security_id") or d.get("isin") or "").strip().upper() != sid:
            continue
        try:
            dd = _to_date(d.get("payment_date") or d.get("ex_date") or d.get("date"))
        except Exception:  # noqa: BLE001, S112
            continue
        if (sd and dd < sd) or (ed and dd > ed):
            continue
        try:
            amt = float(d.get("gross") or d.get("amount") or 0.0)
        except (TypeError, ValueError):
            amt = 0.0
        if amt:
            flows.append((dd, abs(amt), "dividend"))
    if terminal_value and terminal_date is not None:
        try:
            tv = float(terminal_value)
        except (TypeError, ValueError):
            tv = 0.0
        if tv > 0:
            flows.append((_to_date(terminal_date), tv, "terminal_value"))
    flows.sort(key=lambda x: x[0])
    return flows


def stock_xirr(transactions, dividends, security_id, start_date=None, end_date=None, terminal_value=None, terminal_date=None):
    from src.xirr import compute_xirr
    flows = stock_flows(transactions, dividends, security_id, start_date, end_date, terminal_value, terminal_date)
    return compute_xirr([(d, a) for d, a, _ in flows])


def portfolio_flows(transactions, dividends, start_date=None, end_date=None):
    sd = _to_date(start_date) if start_date else None
    ed = _to_date(end_date) if end_date else None
    flows = []
    for t in transactions or []:
        try:
            dd = _to_date(_tx_date(t))
        except Exception:  # noqa: BLE001, S112
            continue
        if (sd and dd < sd) or (ed and dd > ed):
            continue
        try:
            amt = float(t.get("net_amount") or 0.0)
        except (TypeError, ValueError):
            amt = 0.0
        side = _tx_side(t)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            flows.append((dd, -abs(amt)))
        elif side == "SELL":
            flows.append((dd, abs(amt)))
    for d in dividends or []:
        try:
            dd = _to_date(d.get("payment_date") or d.get("ex_date") or d.get("date"))
        except Exception:  # noqa: BLE001, S112
            continue
        if (sd and dd < sd) or (ed and dd > ed):
            continue
        try:
            amt = float(d.get("gross") or d.get("amount") or 0.0)
        except (TypeError, ValueError):
            amt = 0.0
        if amt:
            flows.append((dd, abs(amt)))
    flows.sort(key=lambda x: x[0])
    return flows


def stock_twr(transactions, price_lookup, security_id, start_date=None, end_date=None):
    sid = str(security_id or "").strip().upper()
    if price_lookup is None:
        return None
    sd = _to_date(start_date) if start_date else None
    ed = _to_date(end_date) if end_date else None
    events = []
    for t in transactions or []:
        if str(t.get("isin") or t.get("security") or t.get("symbol") or "").strip().upper() != sid:
            continue
        try:
            dd = _to_date(_tx_date(t))
        except Exception:  # noqa: BLE001, S112
            continue
        if (sd and dd < sd) or (ed and dd > ed):
            continue
        try:
            q = float(t.get("quantity") or 0.0)
        except (TypeError, ValueError):
            q = 0.0
        side = _tx_side(t)
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            events.append((dd, q))
        elif side == "SELL":
            events.append((dd, -q))
    if not events:
        return None
    events.sort(key=lambda x: x[0])
    def _px(dd):
        try:
            p = price_lookup(sid, dd)
        except Exception:  # noqa: BLE001
            return None
        return float(p) if p is not None else None
    qty = 0.0
    cum = 1.0
    v_after = None
    first_date = None
    last_date = None
    for dd, dq in events:
        p = _px(dd)
        if p is None:
            continue
        if first_date is None:
            first_date = dd
        if v_after is not None and v_after > 0:
            r = (qty * p) / v_after - 1.0
            cum *= (1.0 + r)
        qty += dq
        v_after = qty * p
        last_date = dd
    end_d = ed if ed is not None else last_date
    if qty > 0 and end_d is not None and v_after is not None and v_after > 0:
        pe = _px(end_d)
        if pe is not None:
            cum *= (qty * pe) / v_after
            last_date = end_d
    if first_date is None or last_date is None or cum <= 0:
        return None
    days = (last_date - first_date).days
    if days <= 0:
        return 0.0
    try:
        return cum ** (365.0 / days) - 1.0
    except Exception:  # noqa: BLE001
        return None


def benchmark_xirr_for_flows(flows, index_lookup, start_date, end_date):
    from performance.benchmarks import calculate_benchmark_xirr
    cf = []
    for f in flows or []:
        try:
            cf.append((f[0], float(f[1])))
        except (TypeError, ValueError, IndexError):
            continue
    if not cf:
        return None
    try:
        res = calculate_benchmark_xirr(cf, index_lookup, start_date, end_date)
    except Exception:  # noqa: BLE001
        return None
    return res.get("xirr") if isinstance(res, dict) else None


def day_change_pct(price_lookup, security_id, as_of_date):
    if price_lookup is None:
        return None
    sid = str(security_id or "").strip().upper()
    try:
        today = _to_date(as_of_date)
    except Exception:  # noqa: BLE001
        return None
    try:
        now = price_lookup(sid, today)
    except Exception:  # noqa: BLE001
        now = None
    if now is None:
        return None
    prev = None
    for back in range(1, 8):
        try:
            p = price_lookup(sid, today - timedelta(days=back))
        except Exception:  # noqa: BLE001
            p = None
        if p is not None:
            prev = float(p)
            break
    try:
        now = float(now)
    except (TypeError, ValueError):
        return None
    if prev is None or prev <= 0:
        return None
    return now / prev - 1.0


def build_symbol_map(market_db_path="data/market_data.sqlite3"):
    import sqlite3
    out = {}
    try:
        con = sqlite3.connect(market_db_path)
        for sid, sym in con.execute("SELECT security_id, symbol FROM security_symbol_map"):
            k = str(sym or "").strip().upper()
            if k:
                out[k] = str(sid or "").strip().upper()
        con.close()
    except Exception:  # noqa: BLE001
        return {}
    return out


def isin_symbol_map(market_db_path="data/market_data.sqlite3"):
    import sqlite3
    out = {}
    try:
        con = sqlite3.connect(market_db_path)
        for sid, sym in con.execute("SELECT security_id, symbol FROM security_symbol_map"):
            s = str(sid or "").strip().upper()
            k = str(sym or "").strip().upper()
            if s:
                out[s] = (k.split(".")[0] if k else s)
        con.close()
    except Exception:  # noqa: BLE001
        return {}
    return out


def resolve_security(query, symbol_map=None):
    q = str(query or "").strip().upper()
    if not q:
        return None
    if (q.startswith("IN") and len(q) == 12) or q.startswith("INF"):
        return q
    sm = symbol_map or {}
    for cand in (q, q + ".NS", q + ".BO"):
        if cand in sm:
            return sm[cand]
    core = q.split(".")[0]
    for sym, isin in sm.items():
        if sym.split(".")[0] == core:
            return isin
    return None


def get_portfolio_securities(txs, isin_symbol=None):
    isym = isin_symbol or {}
    seen = {}
    for t in txs or []:
        raw = str(t.get("isin") or t.get("security") or "").strip().upper()
        if not raw:
            continue
        if not ((raw.startswith("IN") and len(raw) == 12) or raw.startswith("INF")):
            continue
        seen.setdefault(raw, isym.get(raw, raw))
    rows = [{"isin": i, "label": (s + "  (" + i + ")") if s and s != i else i} for i, s in seen.items()]
    rows.sort(key=lambda r: r["label"])
    return rows


def _price_of(px, d):
    try:
        v = px(d)
    except Exception:  # noqa: BLE001
        return None
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _max_dd(series):
    peak = None
    mdd = 0.0
    for _d, v in series or []:
        if v is None:
            continue
        if peak is None or v > peak:
            peak = v
        if peak and peak > 0:
            dd = v / peak - 1.0
            mdd = min(mdd, dd)
    return mdd


def _simulate_from_txns(txns_a, px, end_date, grid_dates, qty_fallback=False):
    rows = []
    for t in txns_a or []:
        try:
            dd = _to_date(t.get("date") or _tx_date(t))
        except Exception:  # noqa: BLE001, S112
            continue
        side = _tx_side(t)
        try:
            q = float(t.get("quantity") or 0.0)
        except (TypeError, ValueError):
            q = 0.0
        try:
            amt = abs(float(t.get("amount") or t.get("net_amount") or 0.0))
        except (TypeError, ValueError):
            amt = 0.0
        if side in ("BUY", "OPENING", "OPEN", "OPENING_POSITION"):
            rows.append((dd, "BUY", q, amt))
        elif side == "SELL":
            rows.append((dd, "SELL", q, amt))
    rows.sort(key=lambda x: x[0])
    qty_a = 0.0
    units = 0.0
    deployed = 0.0
    realized = 0.0
    cf = []
    timeline = []
    prev_p = None
    for dd, side, q, amt in rows:
        p = _price_of(px, dd)
        if p is not None and p > 0:
            prev_p = p
        use_p = p if (p is not None and p > 0) else prev_p
        if side == "BUY":
            deployed += amt
            qty_a += q
            cf.append((dd, -amt))
            if use_p is not None and use_p > 0:
                units += amt / use_p
            elif qty_fallback and q > 0:
                # No historical price on this buy date: for the actual position
                # fall back to the real transacted quantity so a held stock is
                # never valued at zero (e.g. NCDs / older buys with sparse prices).
                units += q
        else:
            frac = (q / qty_a) if qty_a > 0 else 0.0
            frac = max(0.0, min(1.0, frac))
            sold = frac * units
            cash = sold * (use_p if use_p else 0.0)
            realized += cash
            units -= sold
            if units < 0:
                units = 0.0
            qty_a -= q
            if qty_a < 0:
                qty_a = 0.0
            cf.append((dd, cash))
        timeline.append((dd, units))
    end = _to_date(end_date)
    last_dt = rows[-1][0] if rows else end
    series = []
    for gd in grid_dates:
        if units <= 0 and gd >= last_dt:
            series.append((gd, None))
            continue
        u = 0.0
        for td, tu in timeline:
            if td <= gd:
                u = tu
            else:
                break
        p = _price_of(px, gd)
        series.append((gd, (u * p) if p is not None else None))
    pe = _price_of(px, end)
    if pe is None:
        pe = prev_p
    value_end = (units * pe) if (pe is not None) else None
    from src.xirr import compute_xirr
    flows = list(cf)
    if value_end is not None:
        flows.append((end, value_end))
    xirr = compute_xirr(flows) if len(flows) >= 2 else None
    total_pnl = ((value_end or 0.0) + realized) - deployed
    pnl_pct = (total_pnl / deployed) if deployed else None
    return {"deployed": deployed, "realized": realized, "units": units, "value_end": value_end, "total_pnl": total_pnl, "pnl_pct": pnl_pct, "xirr": xirr, "max_drawdown": _max_dd(series), "series": [(str(d), v) for d, v in series]}


def simulate_cashflow_matched(txns_a, px_actual, px_alt, end_date):
    ds = []
    for t in txns_a or []:
        try:
            ds.append(_to_date(t.get("date") or _tx_date(t)))
        except Exception:  # noqa: BLE001, S112
            continue
    if not ds:
        return None
    start = min(ds)
    end = _to_date(end_date)
    grid = set(_sample_dates(start, end, max_points=96))
    grid |= set(ds)
    grid.add(start)
    grid.add(end)
    grid = sorted(grid)
    return {"actual": _simulate_from_txns(txns_a, px_actual, end_date, grid, qty_fallback=True), "alternative": _simulate_from_txns(txns_a, px_alt, end_date, grid), "grid": [str(d) for d in grid]}


def simulate_lump_sum(amount, px, start, end):
    s = _to_date(start)
    e = _to_date(end)
    p0 = _price_of(px, s)
    if p0 is None or p0 <= 0 or amount <= 0:
        return None
    units = amount / p0
    pe = _price_of(px, e)
    value_end = units * pe if pe is not None else None
    from src.xirr import compute_xirr
    cf = [(s, -amount)]
    if value_end is not None:
        cf.append((e, value_end))
    xirr = compute_xirr(cf) if len(cf) >= 2 else None
    grid = _sample_dates(s, e, max_points=96)
    series = []
    for d in grid:
        p = _price_of(px, d)
        series.append((str(d), (units * p) if p is not None else None))
    return {"invested": amount, "value_end": value_end, "abs_return": (value_end / amount - 1.0) if value_end is not None else None, "xirr": xirr, "max_drawdown": _max_dd([(d, v) for d, v in series]), "series": series}


def simulate_sip(monthly, px, start, end):
    s = _to_date(start)
    e = _to_date(end)
    if monthly <= 0:
        return None
    inst = []
    cur = date(s.year, s.month, 1)
    if cur < s:
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    while cur <= e:
        p = _price_of(px, cur)
        if p is not None and p > 0:
            inst.append((cur, monthly / p))
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    if not inst:
        return None
    units_total = sum(u for _, u in inst)
    invested = monthly * len(inst)
    pe = _price_of(px, e)
    value_end = units_total * pe if pe is not None else None
    from src.xirr import compute_xirr
    cf = [(dd, -monthly) for dd, _u in inst]
    if value_end is not None:
        cf.append((e, value_end))
    xirr = compute_xirr(cf) if len(cf) >= 2 else None
    grid = _sample_dates(s, e, max_points=96)
    series = []
    for gd in grid:
        p = _price_of(px, gd)
        if p is None:
            series.append((str(gd), None))
            continue
        u = sum(units for dd, units in inst if dd <= gd)
        series.append((str(gd), u * p))
    return {"invested": invested, "value_end": value_end, "abs_return": (value_end / invested - 1.0) if (value_end is not None and invested) else None, "xirr": xirr, "max_drawdown": _max_dd([(d, v) for d, v in series]), "series": series, "installments": len(inst)}


def calendar_year_returns(px, start, end):
    s = _to_date(start)
    e = _to_date(end)
    out = []
    for y in range(s.year, e.year + 1):
        ys = max(date(y, 1, 1), s)
        ye = min(date(y, 12, 31), e)
        a = _price_of(px, ys)
        b = _price_of(px, ye)
        if a and b and a > 0:
            out.append({"year": str(y), "return": b / a - 1.0})
    return out
