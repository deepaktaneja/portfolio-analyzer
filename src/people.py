from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime

from src.store import SQLiteStore


def _normalize_tx_date(value) -> str:
    """Normalize transaction dates to a ``YYYY-MM-DD`` string."""
    s = str(value or "").strip()
    if not s:
        return ""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s[:10]


class PersonStore(SQLiteStore):
    def migrate(self) -> None:
        super().migrate()
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS people (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )"""
            )
            for tbl, col, decl in (("transactions", "person_id", "INTEGER"), ("portfolio_snapshots", "person_id", "INTEGER")):
                try:
                    conn.execute("ALTER TABLE " + tbl + " ADD COLUMN " + col + " " + decl)
                except sqlite3.OperationalError:
                    pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_person ON transactions(person_id)")
            conn.commit()

    def list_people(self, active_only: bool = True):
        q = "SELECT id, name, active, created_at FROM people"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY name COLLATE NOCASE"
        with self._conn() as conn:
            rows = conn.execute(q).fetchall()
        return [dict(r) for r in rows]

    def get_person_by_name(self, name: str):
        with self._conn() as conn:
            r = conn.execute("SELECT id, name, active FROM people WHERE name = ? COLLATE NOCASE", (str(name).strip(),)).fetchone()
        return dict(r) if r else None

    def get_person_by_id(self, pid: int):
        with self._conn() as conn:
            r = conn.execute("SELECT id, name, active FROM people WHERE id = ?", (int(pid),)).fetchone()
        return dict(r) if r else None

    def create_person(self, name: str) -> int:
        nm = str(name).strip()
        if not nm:
            raise ValueError("Person name is required")
        with self._conn() as conn:
            r = conn.execute("SELECT id FROM people WHERE name = ? COLLATE NOCASE", (nm,)).fetchone()
            if r:
                return int(r["id"])
            cur = conn.execute("INSERT INTO people (name, active, created_at) VALUES (?, 1, ?)", (nm, datetime.now(UTC).isoformat()))
            conn.commit()
            return int(cur.lastrowid)

    def rename_person(self, pid: int, name: str) -> None:
        nm = str(name).strip()
        if not nm:
            raise ValueError("Person name is required")
        with self._conn() as conn:
            conn.execute("UPDATE people SET name = ? WHERE id = ?", (nm, int(pid)))
            conn.commit()

    def set_person_active(self, pid: int, active: bool) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE people SET active = ? WHERE id = ?", (1 if active else 0, int(pid)))
            conn.commit()

    def person_transaction_counts(self):
        with self._conn() as conn:
            rows = conn.execute("SELECT person_id, COUNT(*) AS c FROM transactions GROUP BY person_id").fetchall()
        return {int(r["person_id"]) if r["person_id"] is not None else -1: int(r["c"]) for r in rows}

    def replace_file_transactions(self, filename: str, rows, person_id=None) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM transactions WHERE source_file = ?", (filename,))
            conn.executemany(
                """INSERT INTO transactions
                   (settlement_date, trade_date, isin, security, buy_sell, quantity,
                    gross_rate, gross_amount, brokerage, gst, charges, net_amount,
                    exchange, order_no, trade_no, page_number, row_index, source_file, fingerprint, person_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        r.get("settlement_date"), r.get("trade_date"), r.get("isin"), r.get("security"),
                        r.get("buy_sell"), r.get("quantity"), r.get("gross_rate"), r.get("gross_amount"),
                        r.get("brokerage"), r.get("gst"), r.get("charges"), r.get("net_amount"),
                        r.get("exchange"), r.get("order_no"), r.get("trade_no"), r.get("page_number"),
                        r.get("row_index"), filename, r.get("fingerprint"), r.get("person_id", person_id),
                    )
                    for r in rows
                ],
            )
            conn.commit()

    def all_transactions(self, person_id=None):
        q = """SELECT settlement_date, trade_date, isin, security, buy_sell, quantity,
                      gross_rate, gross_amount, brokerage, gst, charges, net_amount,
                      exchange, order_no, trade_no, page_number, row_index, source_file, fingerprint, person_id
               FROM transactions"""
        params = ()
        if person_id == -1:
            q += " WHERE person_id IS NULL"
        elif person_id is not None:
            q += " WHERE person_id = ?"
            params = (int(person_id),)
        with self._conn() as conn:
            rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def _transaction_key(self, row: dict) -> tuple[str, str, str, float]:
        trade_date = _normalize_tx_date(row.get("trade_date") or row.get("settlement_date"))
        isin = str(row.get("isin") or row.get("security") or "").strip().upper()
        stock_code = str(row.get("stock_code") or row.get("security") or row.get("isin") or "").strip().upper()
        quantity = float(row.get("quantity") or 0)
        return (trade_date, isin, stock_code, quantity)

    def partition_new_transactions(
        self,
        rows: list[dict],
        person_id: int | None = None,
        exclude_source_file: str | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Split incoming transactions into new and duplicate rows."""
        existing_rows = self.all_transactions(person_id=person_id)
        existing_keys: set[tuple[str, str, str, float]] = set()
        for t in existing_rows or []:
            if exclude_source_file and str(t.get("source_file") or "") == exclude_source_file:
                continue
            existing_keys.add(self._transaction_key(t))

        new_rows: list[dict] = []
        duplicate_rows: list[dict] = []
        seen: set[tuple[str, str, str, float]] = set()
        for row in rows or []:
            key = self._transaction_key(row)
            if key in seen or key in existing_keys:
                duplicate_rows.append(row)
                continue
            seen.add(key)
            new_rows.append(row)
        return new_rows, duplicate_rows

    def add_portfolio_snapshot(self, source_file, as_of_date, detected_type, holdings, person_id=None) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO portfolio_snapshots (source_file, as_of_date, detected_type, imported_at, person_id) VALUES (?,?,?,?,?)",
                (source_file, as_of_date, detected_type, datetime.now(UTC).isoformat(), person_id),
            )
            sid = cur.lastrowid
            conn.executemany(
                """INSERT INTO portfolio_holdings
                   (snapshot_id, isin, symbol, quantity, avg_price, current_price, cost_value, market_value)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    (sid, h.get("isin"), h.get("symbol"), h.get("quantity"), h.get("avg_price"),
                     h.get("current_price"), h.get("cost_value"), h.get("market_value"))
                    for h in holdings
                ],
            )
            conn.commit()
        return int(sid)

    def get_latest_holdings(self, person_id=None):
        with self._conn() as conn:
            if person_id == -1:
                r = conn.execute("SELECT id FROM portfolio_snapshots WHERE person_id IS NULL ORDER BY id DESC LIMIT 1").fetchone()
            elif person_id is not None:
                r = conn.execute("SELECT id FROM portfolio_snapshots WHERE person_id = ? ORDER BY id DESC LIMIT 1", (int(person_id),)).fetchone()
            else:
                r = conn.execute("SELECT id FROM portfolio_snapshots ORDER BY id DESC LIMIT 1").fetchone()
            if not r:
                return []
            sid = int(r["id"])
            rows = conn.execute(
                """SELECT isin, symbol, quantity, avg_price, current_price, cost_value, market_value
                   FROM portfolio_holdings WHERE snapshot_id = ?""",
                (sid,),
            ).fetchall()
        return [dict(x) for x in rows]

    def assigned_transactions(self):
        """Transactions tagged to a person (excludes unassigned rows)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT settlement_date, trade_date, isin, security, buy_sell, quantity,
                          gross_rate, gross_amount, brokerage, gst, charges, net_amount,
                          exchange, order_no, trade_no, page_number, row_index, source_file, fingerprint, person_id
                   FROM transactions WHERE person_id IS NOT NULL"""
            ).fetchall()
        return [dict(r) for r in rows]

    def get_aggregate_holdings(self):
        """Aggregate the latest holdings snapshot of every person (sum across people)."""
        def _num(v):
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return None if math.isnan(f) else f
        with self._conn() as conn:
            pids = [int(r["person_id"]) for r in conn.execute(
                "SELECT DISTINCT person_id FROM portfolio_snapshots WHERE person_id IS NOT NULL"
            )]
        agg = {}
        for pid in pids:
            for h in self.get_latest_holdings(pid):
                mv = _num(h.get("market_value"))
                cp = _num(h.get("current_price"))
                cv = _num(h.get("cost_value"))
                ap = _num(h.get("avg_price"))
                if mv is None and cp is None and cv is None and ap is None:
                    continue
                qty = _num(h.get("quantity")) or 0.0
                isin = str(h.get("isin") or "").strip().upper()
                key = isin or str(h.get("symbol") or "").strip().upper()
                if not key:
                    continue
                rec = agg.get(key)
                if rec is None:
                    rec = {"isin": h.get("isin"), "symbol": h.get("symbol"), "quantity": 0.0, "market_value": 0.0, "cost_value": 0.0, "has_mv": False, "has_cv": False}
                    agg[key] = rec
                rec["quantity"] += qty
                if mv is not None:
                    rec["market_value"] += mv
                    rec["has_mv"] = True
                elif cp is not None:
                    rec["market_value"] += qty * cp
                    rec["has_mv"] = True
                if cv is not None:
                    rec["cost_value"] += cv
                    rec["has_cv"] = True
                elif ap is not None:
                    rec["cost_value"] += qty * ap
                    rec["has_cv"] = True
        out = []
        for rec in agg.values():
            qty = rec["quantity"]
            out.append({
                "isin": rec["isin"],
                "symbol": rec["symbol"],
                "quantity": qty,
                "avg_price": (rec["cost_value"] / qty) if qty and rec["has_cv"] else None,
                "current_price": (rec["market_value"] / qty) if qty and rec["has_mv"] else None,
                "cost_value": rec["cost_value"] if rec["has_cv"] else None,
                "market_value": rec["market_value"] if rec["has_mv"] else None,
            })
        return out
