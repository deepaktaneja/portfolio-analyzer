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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tx_source ON transactions(source_file)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tx_isin ON transactions(isin)"
            )
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
                   (settlement_date, isin, security, buy_sell, quantity, net_amount, source_file)
                   VALUES (?,?,?,?,?,?,?)""",
                [
                    (
                        r.get("settlement_date"),
                        r.get("isin"),
                        r.get("security"),
                        r.get("buy_sell"),
                        r.get("quantity"),
                        r.get("net_amount"),
                        filename,
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
                "SELECT settlement_date, isin, security, buy_sell, quantity, net_amount, source_file FROM transactions"
            ).fetchall()
        return [dict(r) for r in rows]

    def clear(self) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM files")
            conn.commit()