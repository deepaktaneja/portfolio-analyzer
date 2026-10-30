from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

TOL = 1e-9


class MarketDataStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def migrate(self) -> None:
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS stock_prices (security_id TEXT NOT NULL, exchange TEXT NOT NULL, trade_date TEXT NOT NULL, close_price REAL NOT NULL, price_type TEXT NOT NULL DEFAULT "raw", source TEXT, imported_at TEXT NOT NULL, raw_value REAL, PRIMARY KEY (security_id, exchange, trade_date))""")
            c.execute("""CREATE TABLE IF NOT EXISTS index_tri (index_id TEXT NOT NULL, trade_date TEXT NOT NULL, level REAL NOT NULL, source TEXT, imported_at TEXT NOT NULL, raw_value REAL, PRIMARY KEY (index_id, trade_date))""")
            c.execute("""CREATE TABLE IF NOT EXISTS corporate_action_events (security_id TEXT NOT NULL, event_type TEXT NOT NULL, ex_date TEXT NOT NULL, details TEXT, source TEXT, imported_at TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS symbol_changes (old_id TEXT NOT NULL, new_id TEXT NOT NULL, effective_date TEXT NOT NULL, source TEXT, imported_at TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS import_batches (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, source_count INTEGER NOT NULL DEFAULT 0, row_count INTEGER NOT NULL DEFAULT 0)""")
            c.commit()


    def upsert_stock_prices(self, rows):
        from datetime import UTC, datetime
        n = 0
        with self._conn() as c:
            for r in rows:
                c.execute("INSERT INTO stock_prices (security_id, exchange, trade_date, close_price, price_type, source, imported_at, raw_value) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(security_id, exchange, trade_date) DO UPDATE SET close_price=excluded.close_price, price_type=excluded.price_type, source=excluded.source, imported_at=excluded.imported_at, raw_value=excluded.raw_value", (r["security_id"], r.get("exchange", "NSE"), r["trade_date"], float(r["close_price"]), r.get("price_type", "raw"), r.get("source"), datetime.now(UTC).isoformat(), r.get("raw_value", r["close_price"])))
                n += 1
            c.commit()
        return n

    def upsert_index_tri(self, rows):
        n = 0
        with self._conn() as c:
            for r in rows:
                c.execute("INSERT INTO index_tri (index_id, trade_date, level, source, imported_at, raw_value) VALUES (?,?,?,?,?,?) ON CONFLICT(index_id, trade_date) DO UPDATE SET level=excluded.level, source=excluded.source, imported_at=excluded.imported_at, raw_value=excluded.raw_value", (r["index_id"], r["trade_date"], float(r["level"]), r.get("source"), datetime.now(UTC).isoformat(), r.get("raw_value", r["level"])))
                n += 1
            c.commit()
        return n


    def get_stock_price(self, security_id, date, exchange=None, allow_last=True):
        sid = security_id.strip().upper()
        with self._conn() as c:
            if exchange:
                row = c.execute("SELECT close_price, trade_date FROM stock_prices WHERE security_id=? AND exchange=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1", (sid, exchange, date)).fetchone()
            else:
                row = c.execute("SELECT close_price, trade_date FROM stock_prices WHERE security_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1", (sid, date)).fetchone()
        if row is None:
            return None
        if not allow_last and row["trade_date"] != date:
            return None
        return float(row["close_price"])

    def get_stock_prices(self, security_id, start, end, exchange=None):
        sid = security_id.strip().upper()
        with self._conn() as c:
            if exchange:
                rows = c.execute("SELECT trade_date, close_price FROM stock_prices WHERE security_id=? AND exchange=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date", (sid, exchange, start, end)).fetchall()
            else:
                rows = c.execute("SELECT trade_date, close_price FROM stock_prices WHERE security_id=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date", (sid, start, end)).fetchall()
        return [(r["trade_date"], float(r["close_price"])) for r in rows]

    def get_index_tri(self, index_id, date, allow_last=True):
        iid = index_id.strip().upper()
        with self._conn() as c:
            row = c.execute("SELECT level, trade_date FROM index_tri WHERE index_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1", (iid, date)).fetchone()
        if row is None:
            return None
        if not allow_last and row["trade_date"] != date:
            return None
        return float(row["level"])


    def get_missing_prices(self, start, end, security_ids=None):
        from datetime import date as _d
        from datetime import timedelta
        s = _d.fromisoformat(start)
        e = _d.fromisoformat(end)
        expected = []
        cur = s
        while cur <= e:
            if cur.weekday() < 5:
                expected.append(cur.isoformat())
            cur += timedelta(days=1)
        missing = []
        if security_ids is None:
            with self._conn() as c:
                security_ids = [r[0] for r in c.execute("SELECT DISTINCT security_id FROM stock_prices").fetchall()]
        for sid in security_ids:
            sid = sid.strip().upper()
            with self._conn() as c:
                have = {r[0] for r in c.execute("SELECT trade_date FROM stock_prices WHERE security_id=? AND trade_date BETWEEN ? AND ?", (sid, start, end)).fetchall()}
            for d in expected:
                if d not in have:
                    missing.append((sid, d))
        return missing

    def get_market_data_quality_report(self):
        with self._conn() as c:
            secs = [r[0] for r in c.execute("SELECT DISTINCT security_id FROM stock_prices").fetchall()]
            idxs = [r[0] for r in c.execute("SELECT DISTINCT index_id FROM index_tri").fetchall()]
            anomalies = []
            for r in c.execute("SELECT security_id, exchange, trade_date, close_price FROM stock_prices WHERE close_price<=0").fetchall():
                anomalies.append({"type": "non_positive_price", "security_id": r["security_id"], "exchange": r["exchange"], "trade_date": r["trade_date"], "value": r["close_price"]})
            for sid in secs:
                rows = c.execute("SELECT trade_date, close_price FROM stock_prices WHERE security_id=? ORDER BY trade_date", (sid,)).fetchall()
                prev = None
                for r in rows:
                    if prev is not None and prev["close_price"] > 0:
                        move = abs(r["close_price"] / prev["close_price"] - 1.0)
                        if move > 0.5:
                            anomalies.append({"type": "large_move", "security_id": sid, "trade_date": r["trade_date"], "move": move})
                    prev = r
            coverage = {}
            for sid in secs:
                cnt = c.execute("SELECT COUNT(*) FROM stock_prices WHERE security_id=?", (sid,)).fetchone()[0]
                mn, mx = c.execute("SELECT MIN(trade_date), MAX(trade_date) FROM stock_prices WHERE security_id=?", (sid,)).fetchone()
                coverage[sid] = {"rows": cnt, "from": mn, "to": mx}
            index_coverage = {}
            for iid in idxs:
                cnt = c.execute("SELECT COUNT(*) FROM index_tri WHERE index_id=?", (iid,)).fetchone()[0]
                mn, mx = c.execute("SELECT MIN(trade_date), MAX(trade_date) FROM index_tri WHERE index_id=?", (iid,)).fetchone()
                index_coverage[iid] = {"rows": cnt, "from": mn, "to": mx}
        return {"securities": secs, "indexes": idxs, "stock_coverage": coverage, "index_coverage": index_coverage, "anomalies": anomalies}

