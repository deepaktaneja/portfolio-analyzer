from __future__ import annotations
import sqlite3
from collections import defaultdict
SYNTH_SOURCE = "__opening_2014_01_01__"
def norm(s):
    return "".join(c for c in str(s or "").lower() if c.isalnum())
def analyze(db_path, person_id):
    xc = sqlite3.connect(db_path)
    rows = xc.execute("SELECT id, isin, security, buy_sell, quantity, net_amount FROM transactions WHERE person_id=? AND source_file!=?", (person_id, SYNTH_SOURCE)).fetchall()
    net = defaultdict(float)
    nm2isin = {}
    ids = defaultdict(list)
    for rid, i, s, b, q, a in rows:
        k = (i.strip().upper() if i and i.strip() else (s or "").strip().upper())
        if not k:
            continue
        ids[k].append(rid)
        q = float(q or 0.0)
        if i and i.strip() and s:
            nm2isin[norm(s)] = i.strip().upper()
        if (b or "").upper() == "BUY":
            net[k] += q
        else:
            net[k] -= q
    unmatched = dict((k, -v) for k, v in net.items() if v < -0.000001)
    return xc, unmatched, nm2isin, ids
def symbol_map(market_db):
    try:
        mc = sqlite3.connect(market_db)
        return dict(mc.execute("SELECT security_id, provider_symbol FROM security_symbol_map").fetchall())
    except Exception:
        return {}
def resolve_symbol(key, sym, nm2isin):
    if sym.get(key):
        return sym[key]
    kk = norm(key)
    for nm, isn in nm2isin.items():
        if nm and (nm in kk or kk in nm) and min(len(nm), len(kk)) >= 4 and sym.get(isn):
            return sym[isn]
    return None
def price_on_2014(ticker, buy_date="2014-01-01"):
    import yfinance as yf
    h = yf.Ticker(ticker).history(start="2013-12-30", end="2014-01-04", auto_adjust=False)
    c = h["Close"].dropna()
    if not len(c):
        return None
    return float(c.iloc[-1])
def set_2014_buy_date(db_path, person_id, market_db, buy_date="2014-01-01", drop_unpriced=False, progress=None):
    xc, unmatched, nm2isin, ids = analyze(db_path, person_id)
    sym = symbol_map(market_db)
    xc.execute("DELETE FROM transactions WHERE source_file=? AND person_id=?", (SYNTH_SOURCE, person_id))
    priced = 0
    dropped = 0
    skipped = 0
    total_cost = 0.0
    items = sorted(unmatched.items(), key=lambda kv: -kv[1])
    total = len(items)
    for idx, (k, qty) in enumerate(items):
        tick = resolve_symbol(k, sym, nm2isin)
        price = None
        if tick:
            try:
                price = price_on_2014(tick, buy_date)
            except Exception:
                price = None
        if price and price > 0:
            isin = k if (len(k) == 12 and k[:2].isalpha()) else None
            xc.execute("INSERT INTO transactions (settlement_date,trade_date,isin,security,buy_sell,quantity,gross_rate,gross_amount,net_amount,source_file,fingerprint,person_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (buy_date, buy_date, isin, k, "BUY", qty, price, qty * price, qty * price, SYNTH_SOURCE, "synthetic-open-2014:" + k, person_id))
            priced += 1
            total_cost += qty * price
        elif drop_unpriced:
            for rid in ids.get(k, []):
                xc.execute("DELETE FROM transactions WHERE id=?", (rid,))
            dropped += 1
        else:
            skipped += 1
        if progress:
            progress(idx + 1, total, k)
    xc.commit()
    xc.close()
    return {"unmatched": len(unmatched), "priced": priced, "dropped": dropped, "skipped": skipped, "total_cost": total_cost}
