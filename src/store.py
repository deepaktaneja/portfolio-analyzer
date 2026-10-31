"""SQLite persistence for parsed transaction statements."""
from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class SQLiteStore:
    def __init__(self, db_path: str | os.PathLike):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def migrate(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS files (
                    filename TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    parsed_at TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    settlement_date TEXT,
                    isin TEXT,
                    security TEXT,
                    buy_sell TEXT,
                    quantity REAL,
                    net_amount REAL,
                    source_file TEXT NOT NULL
                )"""
            )
            # backfill columns for older DBs
            for col, decl in (
                ("trade_date", "TEXT"),
                ("gross_rate", "REAL"),
                ("gross_amount", "REAL"),
                ("brokerage", "REAL"),
                ("gst", "REAL"),
                ("charges", "REAL"),
                ("exchange", "TEXT"),
                ("order_no", "TEXT"),
                ("trade_no", "TEXT"),
                ("page_number", "INTEGER"),
                ("row_index", "INTEGER"),
                ("fingerprint", "TEXT"),
            ):
                try:
                    conn.execute(f"ALTER TABLE transactions ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError:
                    pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_source ON transactions(source_file)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_isin ON transactions(isin)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_fingerprint ON transactions(fingerprint)")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS import_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    transaction_count INTEGER NOT NULL DEFAULT 0
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS source_documents (
                    filename TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    detected_type TEXT,
                    imported_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_file TEXT NOT NULL,
                    as_of_date TEXT,
                    detected_type TEXT,
                    imported_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS portfolio_holdings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL,
                    isin TEXT,
                    symbol TEXT,
                    quantity REAL,
                    avg_price REAL,
                    current_price REAL,
                    cost_value REAL,
                    market_value REAL,
                    FOREIGN KEY(snapshot_id) REFERENCES portfolio_snapshots(id)
                )"""
            )
            conn.commit()

    def _ensure_opening_table(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS opening_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    security_id TEXT NOT NULL,
                    ledger_opening_date TEXT NOT NULL,
                    opening_qty REAL NOT NULL,
                    original_acq_date TEXT,
                    original_acq_cost REAL,
                    source TEXT,
                    verification_status TEXT NOT NULL DEFAULT 'unresolved',
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (security_id, ledger_opening_date)
                )"""
            )
            conn.commit()

    def add_opening_position(self, security_id, ledger_opening_date, opening_qty, original_acq_date=None, original_acq_cost=None, source=None, verification_status='unresolved', notes=None):
        self._ensure_opening_table()
        from datetime import datetime
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO opening_positions (security_id, ledger_opening_date, opening_qty, original_acq_date, original_acq_cost, source, verification_status, notes, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(security_id, ledger_opening_date) DO UPDATE SET
                     opening_qty=excluded.opening_qty,
                     original_acq_date=excluded.original_acq_date,
                     original_acq_cost=excluded.original_acq_cost,
                     source=excluded.source,
                     verification_status=excluded.verification_status,
                     notes=excluded.notes""",
                (security_id.strip().upper(), ledger_opening_date, float(opening_qty), original_acq_date, original_acq_cost, source, verification_status, notes, datetime.now(UTC).isoformat()),
            )
            conn.commit()

    def get_opening_positions(self, security_id=None):
        self._ensure_opening_table()
        with self._conn() as conn:
            if security_id:
                rows = conn.execute("SELECT * FROM opening_positions WHERE security_id=?", (security_id.strip().upper(),)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM opening_positions").fetchall()
        return [dict(r) for r in rows]

    def delete_opening_position(self, security_id, ledger_opening_date):
        self._ensure_opening_table()
        with self._conn() as conn:
            conn.execute("DELETE FROM opening_positions WHERE security_id=? AND ledger_opening_date=?", (security_id.strip().upper(), ledger_opening_date))
            conn.commit()

    def known_files(self) -> dict[str, float]:
        with self._conn() as conn:
            rows = conn.execute("SELECT filename, mtime FROM files").fetchall()
        return {r["filename"]: float(r["mtime"]) for r in rows}

    def file_row_count(self, filename: str) -> int:
        with self._conn() as conn:
            r = conn.execute(
                "SELECT COUNT(*) AS c FROM transactions WHERE source_file=?", (filename,)
            ).fetchone()
        return int(r["c"]) if r else 0

    def replace_file_transactions(self, filename: str, rows: list[dict]) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM transactions WHERE source_file=?", (filename,))
            conn.executemany(
                """INSERT INTO transactions
                   (settlement_date, trade_date, isin, security, buy_sell, quantity,
                    gross_rate, gross_amount, brokerage, gst, charges, net_amount,
                    exchange, order_no, trade_no, page_number, row_index, source_file, fingerprint)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        r.get("settlement_date"),
                        r.get("trade_date"),
                        r.get("isin"),
                        r.get("security"),
                        r.get("buy_sell"),
                        r.get("quantity"),
                        r.get("gross_rate"),
                        r.get("gross_amount"),
                        r.get("brokerage"),
                        r.get("gst"),
                        r.get("charges"),
                        r.get("net_amount"),
                        r.get("exchange"),
                        r.get("order_no"),
                        r.get("trade_no"),
                        r.get("page_number"),
                        r.get("row_index"),
                        filename,
                        r.get("fingerprint"),
                    )
                    for r in rows
                ],
            )
            conn.commit()

    def upsert_file(self, filename: str, mtime: float, row_count: int) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO files (filename, mtime, parsed_at, row_count)
                   VALUES (?,?,?,?)
                   ON CONFLICT(filename) DO UPDATE SET
                     mtime=excluded.mtime,
                     parsed_at=excluded.parsed_at,
                     row_count=excluded.row_count""",
                (filename, mtime, datetime.now(UTC).isoformat(), row_count),
            )
            conn.commit()

    def remove_file(self, filename: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM transactions WHERE source_file=?", (filename,))
            conn.execute("DELETE FROM files WHERE filename=?", (filename,))
            conn.commit()

    def all_transactions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT settlement_date, trade_date, isin, security, buy_sell, quantity,
                          gross_rate, gross_amount, brokerage, gst, charges, net_amount,
                          exchange, order_no, trade_no, page_number, row_index, source_file, fingerprint
                   FROM transactions"""
            ).fetchall()
        return [dict(r) for r in rows]

    def clear(self) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM files")
            conn.commit()

    def add_portfolio_snapshot(self, source_file: str, as_of_date: str | None, detected_type: str | None, holdings: list[dict]) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO portfolio_snapshots (source_file, as_of_date, detected_type, imported_at) VALUES (?,?,?,?)",
                (source_file, as_of_date, detected_type, datetime.now(UTC).isoformat()),
            )
            sid = cur.lastrowid
            conn.executemany(
                """INSERT INTO portfolio_holdings
                   (snapshot_id, isin, symbol, quantity, avg_price, current_price, cost_value, market_value)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    (
                        sid,
                        h.get("isin"),
                        h.get("symbol"),
                        h.get("quantity"),
                        h.get("avg_price"),
                        h.get("current_price"),
                        h.get("cost_value"),
                        h.get("market_value"),
                    )
                    for h in holdings
                ],
            )
            conn.commit()
        return int(sid)