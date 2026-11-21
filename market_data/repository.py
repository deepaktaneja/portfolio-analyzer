from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

TOL = 1e-9


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
            c.execute('''CREATE TABLE IF NOT EXISTS stock_prices (security_id TEXT NOT NULL, exchange TEXT NOT NULL, trade_date TEXT NOT NULL, close_price REAL NOT NULL, price_type TEXT NOT NULL DEFAULT "raw", source TEXT, imported_at TEXT NOT NULL, raw_value REAL, PRIMARY KEY (security_id, exchange, trade_date))''')
            c.execute('''CREATE TABLE IF NOT EXISTS index_tri (index_id TEXT NOT NULL, trade_date TEXT NOT NULL, level REAL NOT NULL, source TEXT, imported_at TEXT NOT NULL, raw_value REAL, PRIMARY KEY (index_id, trade_date))''')
            c.execute('''CREATE TABLE IF NOT EXISTS corporate_action_events (security_id TEXT NOT NULL, event_type TEXT NOT NULL, ex_date TEXT NOT NULL, details TEXT, source TEXT, imported_at TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS symbol_changes (old_id TEXT NOT NULL, new_id TEXT NOT NULL, effective_date TEXT NOT NULL, source TEXT, imported_at TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS import_batches (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, source_count INTEGER NOT NULL DEFAULT 0, row_count INTEGER NOT NULL DEFAULT 0)''')

            c.execute('''CREATE TABLE IF NOT EXISTS security_symbol_map (id INTEGER PRIMARY KEY AUTOINCREMENT, security_id TEXT NOT NULL, symbol TEXT, provider_symbol TEXT, exchange TEXT, provider TEXT, verified INTEGER NOT NULL DEFAULT 0, verification_status TEXT NOT NULL DEFAULT 'unverified', source TEXT, imported_at TEXT NOT NULL, updated_at TEXT, notes TEXT, UNIQUE(security_id, provider))''')
            c.execute('''CREATE TABLE IF NOT EXISTS corporate_actions (id INTEGER PRIMARY KEY AUTOINCREMENT, security_id TEXT NOT NULL, event_type TEXT NOT NULL, ex_date TEXT, record_date TEXT, effective_date TEXT, ratio REAL, successor_id TEXT, details TEXT, source TEXT, provider TEXT, raw_value REAL, fetched_at TEXT, imported_at TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, verification_status TEXT NOT NULL DEFAULT 'provisional', verified_by TEXT, verified_at TEXT, notes TEXT, UNIQUE(security_id, event_type, ex_date, ratio, successor_id))''')
            c.execute('''CREATE TABLE IF NOT EXISTS dividend_events (id INTEGER PRIMARY KEY AUTOINCREMENT, security_id TEXT NOT NULL, ex_date TEXT, record_date TEXT, payment_date TEXT, dividend_per_share REAL, currency TEXT, source TEXT, provider TEXT, raw_value REAL, fetched_at TEXT, imported_at TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, verification_status TEXT NOT NULL DEFAULT 'provisional', UNIQUE(security_id, ex_date, dividend_per_share))''')
            c.execute('''CREATE TABLE IF NOT EXISTS benchmark_history (id INTEGER PRIMARY KEY AUTOINCREMENT, index_id TEXT NOT NULL, trade_date TEXT NOT NULL, level REAL NOT NULL, close REAL, source TEXT, provider TEXT, raw_value REAL, fetched_at TEXT, imported_at TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, verification_status TEXT NOT NULL DEFAULT 'unverified', UNIQUE(index_id, trade_date))''')
            c.execute('''CREATE TABLE IF NOT EXISTS market_data_sync_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, status TEXT, provider TEXT, scope TEXT, security_count INTEGER NOT NULL DEFAULT 0, rows_inserted INTEGER NOT NULL DEFAULT 0, rows_updated INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0, error_text TEXT, details TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS watchlist_stocks (id INTEGER PRIMARY KEY AUTOINCREMENT, isin TEXT, nse_ticker TEXT NOT NULL, company_name TEXT, manual_cmp REAL, added_at TEXT NOT NULL, UNIQUE(nse_ticker))''')
            c.execute('''CREATE TABLE IF NOT EXISTS backtest_skipped_tickers (ticker TEXT PRIMARY KEY, skipped_at TEXT NOT NULL, reason TEXT)''')
            c.commit()

            for tbl, cols in (("stock_prices", [("raw_close", "REAL"), ("adjusted_close", "REAL"), ("provider", "TEXT"), ("fetched_at", "TEXT"), ("verification_status", "TEXT"), ("currency", "TEXT")]), ("index_tri", [("provider", "TEXT"), ("fetched_at", "TEXT"), ("verification_status", "TEXT"), ("raw_level", "REAL")])):
                for col, decl in cols:
                    try:
                        c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")
                    except sqlite3.OperationalError:
                        pass
            # corporate_actions verification columns
            for col, decl in (("verified_by", "TEXT"), ("verified_at", "TEXT"), ("notes", "TEXT")):
                try:
                    c.execute(f"ALTER TABLE corporate_actions ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError:
                    pass
            c.commit()

    def _should_preserve(self, existing_status, new_status, preserve_verified):
        if not preserve_verified:
            return False
        es = (existing_status or "").lower()
        ns = (new_status or "").lower()
        return es in ("verified", "manual") and ns not in ("verified", "manual")
    def upsert_stock_prices(self, rows, preserve_verified=True, **kwargs):
        n = 0
        now = _now()
        with self._conn() as c:
            for r in rows or []:
                sid = str(r["security_id"]).strip().upper()
                ex = str(r.get("exchange", "NSE") or "NSE").strip().upper()
                td = str(r["trade_date"]).strip()
                close = float(r["close_price"] if "close_price" in r else r.get("close"))
                price_type = r.get("price_type", "raw") or "raw"
                source = r.get("source")
                raw_value = r.get("raw_value", r.get("raw_close", r.get("raw_price", close)))
                try:
                    raw_value = float(raw_value) if raw_value is not None else close
                except (TypeError, ValueError):
                    raw_value = close
                adjusted = r.get("adjusted_close", r.get("adj_close", r.get("adjusted_close_price")))
                try:
                    adjusted = float(adjusted) if adjusted is not None else None
                except (TypeError, ValueError):
                    adjusted = None
                provider = r.get("provider", source)
                fetched_at = r.get("fetched_at", now)
                vstatus = r.get("verification_status", r.get("status", "unverified")) or "unverified"
                currency = r.get("currency")

                exrow = c.execute("SELECT verification_status FROM stock_prices WHERE security_id=? AND exchange=? AND trade_date=?", (sid, ex, td)).fetchone()
                if exrow and self._should_preserve(exrow["verification_status"], vstatus, preserve_verified):
                    n += 1
                    continue

                c.execute('''INSERT INTO stock_prices (security_id, exchange, trade_date, close_price, price_type, source, imported_at, raw_value, raw_close, adjusted_close, provider, fetched_at, verification_status, currency) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(security_id, exchange, trade_date) DO UPDATE SET close_price=excluded.close_price, price_type=excluded.price_type, source=excluded.source, imported_at=excluded.imported_at, raw_value=excluded.raw_value, raw_close=excluded.raw_close, adjusted_close=excluded.adjusted_close, provider=excluded.provider, fetched_at=excluded.fetched_at, verification_status=excluded.verification_status, currency=excluded.currency''', (sid, ex, td, close, price_type, source, now, raw_value, raw_value, adjusted, provider, fetched_at, vstatus, currency))
                n += 1
            c.commit()
        return n
    def upsert_index_tri(self, rows, preserve_verified=True, **kwargs):
        n = 0
        now = _now()
        with self._conn() as c:
            for r in rows or []:
                iid = str(r["index_id"]).strip().upper()
                td = str(r["trade_date"]).strip()
                level = float(r["level"] if "level" in r else r.get("close"))
                source = r.get("source")
                raw_value = r.get("raw_value", r.get("raw_level", level))
                try:
                    raw_value = float(raw_value) if raw_value is not None else level
                except (TypeError, ValueError):
                    raw_value = level
                provider = r.get("provider", source)
                fetched_at = r.get("fetched_at", now)
                vstatus = r.get("verification_status", r.get("status", "unverified")) or "unverified"

                exrow = c.execute("SELECT verification_status FROM index_tri WHERE index_id=? AND trade_date=?", (iid, td)).fetchone()
                if exrow and self._should_preserve(exrow["verification_status"], vstatus, preserve_verified):
                    n += 1
                    continue

                c.execute('''INSERT INTO index_tri (index_id, trade_date, level, source, imported_at, raw_value, provider, fetched_at, verification_status, raw_level) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(index_id, trade_date) DO UPDATE SET level=excluded.level, source=excluded.source, imported_at=excluded.imported_at, raw_value=excluded.raw_value, provider=excluded.provider, fetched_at=excluded.fetched_at, verification_status=excluded.verification_status, raw_level=excluded.raw_level''', (iid, td, level, source, now, raw_value, provider, fetched_at, vstatus, raw_value))
                n += 1
            c.commit()
        return n

    def upsert_benchmark_history(self, rows, preserve_verified=True, **kwargs):
        n = 0
        now = _now()
        with self._conn() as c:
            for r in rows or []:
                iid = str(r["index_id"] if "index_id" in r else r.get("benchmark_id", r.get("symbol"))).strip().upper()
                td = str(r["trade_date"] if "trade_date" in r else r.get("date")).strip()
                level = float(r["level"] if "level" in r else r.get("close", r.get("value")))
                close = r.get("close", level)
                try:
                    close = float(close) if close is not None else level
                except (TypeError, ValueError):
                    close = level
                source = r.get("source")
                raw_value = r.get("raw_value", r.get("raw_level", level))
                try:
                    raw_value = float(raw_value) if raw_value is not None else level
                except (TypeError, ValueError):
                    raw_value = level
                provider = r.get("provider", source)
                fetched_at = r.get("fetched_at", now)
                vstatus = r.get("verification_status", r.get("status", "unverified")) or "unverified"

                exrow = c.execute("SELECT verification_status FROM benchmark_history WHERE index_id=? AND trade_date=?", (iid, td)).fetchone()
                if exrow and self._should_preserve(exrow["verification_status"], vstatus, preserve_verified):
                    n += 1
                    continue

                c.execute('''INSERT INTO benchmark_history (index_id, trade_date, level, close, source, provider, raw_value, fetched_at, imported_at, verification_status) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(index_id, trade_date) DO UPDATE SET level=excluded.level, close=excluded.close, source=excluded.source, provider=excluded.provider, raw_value=excluded.raw_value, fetched_at=excluded.fetched_at, imported_at=excluded.imported_at, verification_status=excluded.verification_status''', (iid, td, level, close, source, provider, raw_value, fetched_at, now, vstatus))
                n += 1
            c.commit()
        try:
            mirror = []
            for r in rows or []:
                mirror.append({"index_id": r["index_id"] if "index_id" in r else r.get("benchmark_id", r.get("symbol")), "trade_date": r["trade_date"] if "trade_date" in r else r.get("date"), "level": r["level"] if "level" in r else r.get("close", r.get("value")), "source": r.get("source"), "provider": r.get("provider", r.get("source")), "raw_value": r.get("raw_value"), "fetched_at": r.get("fetched_at"), "verification_status": r.get("verification_status", r.get("status"))})
            self.upsert_index_tri(mirror, preserve_verified=preserve_verified)
        except Exception:
            pass
        return n

    def upsert_security_symbol_map(self, rows, preserve_verified=True, **kwargs):
        return self.upsert_symbol_map(rows, preserve_verified=preserve_verified, **kwargs)

    def upsert_symbol_map(self, rows, preserve_verified=True, **kwargs):
        n = 0
        now = _now()
        with self._conn() as c:
            for r in rows or []:
                sid = str(r["security_id"]).strip().upper()
                symbol = r.get("symbol", r.get("ticker"))
                provider_symbol = r.get("provider_symbol", r.get("providerSymbol", symbol))
                exchange = (r.get("exchange") or "").strip().upper() or None
                provider = r.get("provider", r.get("source", "manual"))
                source = r.get("source", provider)
                vstatus = r.get("verification_status", r.get("status", "unverified")) or "unverified"
                verified = 1 if (r.get("verified", False) or str(vstatus).lower() in ("verified", "manual")) else 0
                notes = r.get("notes")

                exrow = c.execute("SELECT verification_status FROM security_symbol_map WHERE security_id=? AND COALESCE(provider,'')=COALESCE(?, '')", (sid, provider)).fetchone()
                if exrow and self._should_preserve(exrow["verification_status"], vstatus, preserve_verified):
                    n += 1
                    continue

                c.execute('''INSERT INTO security_symbol_map (security_id, symbol, provider_symbol, exchange, provider, verified, verification_status, source, imported_at, updated_at, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(security_id, provider) DO UPDATE SET symbol=excluded.symbol, provider_symbol=excluded.provider_symbol, exchange=excluded.exchange, verified=excluded.verified, verification_status=excluded.verification_status, source=excluded.source, updated_at=excluded.updated_at, notes=excluded.notes''', (sid, symbol, provider_symbol, exchange, provider, verified, vstatus, source, now, now, notes))
                n += 1
            c.commit()
        return n

    def get_security_symbol_map(self, security_id=None, provider=None, **kwargs):
        return self.get_symbol_map(security_id=security_id, provider=provider, **kwargs)

    def get_symbol_map(self, security_id=None, provider=None, **kwargs):
        with self._conn() as c:
            q = "SELECT * FROM security_symbol_map WHERE 1=1"
            params = []
            if security_id is not None:
                q += " AND security_id=?"
                params.append(str(security_id).strip().upper())
            if provider is not None:
                q += " AND provider=?"
                params.append(provider)
            rows = [dict(r) for r in c.execute(q, params).fetchall()]
        return rows

    def list_security_symbol_map(self, **kwargs):
        return self.get_symbol_map(**kwargs)

    # ---- Persistent watchlist (Simulate New Stock Entry / incubation planner) ----
    def get_watchlist_stocks(self):
        """Return every persisted watchlist stock ordered by insertion time."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, isin, nse_ticker, company_name, manual_cmp, added_at "
                "FROM watchlist_stocks ORDER BY added_at, id"
            ).fetchall()
        return [dict(r) for r in rows]

    def add_watchlist_stock(self, isin=None, nse_ticker=None, company_name=None, manual_cmp=None):
        """Insert (or refresh) a watchlist stock keyed by its canonical NSE ticker.

        Returns the row id. Upserts on ``nse_ticker`` so re-adding the same stock
        never duplicates the row (idempotent POST semantics).
        """
        ticker = str(nse_ticker or "").strip().upper()
        if not ticker:
            raise ValueError("nse_ticker is required to add a watchlist stock")
        isin_val = str(isin).strip().upper() if isin else None
        name_val = str(company_name).strip() if company_name else None
        cmp_val = float(manual_cmp) if manual_cmp is not None else None
        with self._conn() as c:
            existing = c.execute(
                "SELECT id FROM watchlist_stocks WHERE nse_ticker=?", (ticker,)
            ).fetchone()
            if existing is not None:
                c.execute(
                    "UPDATE watchlist_stocks SET isin=COALESCE(?, isin), "
                    "company_name=COALESCE(?, company_name), "
                    "manual_cmp=COALESCE(?, manual_cmp) WHERE id=?",
                    (isin_val, name_val, cmp_val, existing["id"]),
                )
                c.commit()
                return int(existing["id"])
            cur = c.execute(
                "INSERT INTO watchlist_stocks (isin, nse_ticker, company_name, manual_cmp, added_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (isin_val, ticker, name_val, cmp_val, _now()),
            )
            c.commit()
            return int(cur.lastrowid)

    def delete_watchlist_stock(self, stock_id=None, nse_ticker=None):
        """Delete one watchlist stock by ``id`` or ``nse_ticker`` (DELETE semantics)."""
        with self._conn() as c:
            if stock_id is not None:
                cur = c.execute("DELETE FROM watchlist_stocks WHERE id=?", (int(stock_id),))
            elif nse_ticker:
                cur = c.execute(
                    "DELETE FROM watchlist_stocks WHERE nse_ticker=?",
                    (str(nse_ticker).strip().upper(),),
                )
            else:
                return 0
            c.commit()
            return int(cur.rowcount or 0)

    def get_watchlist_stock(self, isin=None, nse_ticker=None):
        """Look up a single watchlist row by ISIN or NSE ticker (for name/link resolution)."""
        with self._conn() as c:
            if nse_ticker:
                row = c.execute(
                    "SELECT id, isin, nse_ticker, company_name, manual_cmp, added_at "
                    "FROM watchlist_stocks WHERE nse_ticker=?",
                    (str(nse_ticker).strip().upper(),),
                ).fetchone()
            elif isin:
                row = c.execute(
                    "SELECT id, isin, nse_ticker, company_name, manual_cmp, added_at "
                    "FROM watchlist_stocks WHERE isin=?",
                    (str(isin).strip().upper(),),
                ).fetchone()
            else:
                row = None
        return dict(row) if row is not None else None

    # ---- Backtest skip markers (delisted / no-data tickers) ----
    def get_skipped_tickers(self):
        """Return persisted skip markers so delisted tickers are not retried."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT ticker, skipped_at, reason FROM backtest_skipped_tickers"
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_skipped_tickers(self, tickers, reason="no_data"):
        """Persist tickers to skip on future backtest cache runs (idempotent)."""
        now = _now()
        with self._conn() as c:
            for t in tickers or []:
                tk = str(t).strip().upper()
                if not tk:
                    continue
                c.execute(
                    "INSERT INTO backtest_skipped_tickers (ticker, skipped_at, reason) "
                    "VALUES (?, ?, ?) ON CONFLICT(ticker) DO UPDATE SET "
                    "skipped_at=excluded.skipped_at, reason=excluded.reason",
                    (tk, now, reason),
                )
            c.commit()

    def clear_skipped_tickers(self, tickers=None):
        """Clear skip markers (all, or only the given tickers)."""
        with self._conn() as c:
            if tickers:
                for t in tickers:
                    c.execute("DELETE FROM backtest_skipped_tickers WHERE ticker=?",
                              (str(t).strip().upper(),))
            else:
                c.execute("DELETE FROM backtest_skipped_tickers")
            c.commit()

    def _ca_to_dict(self, r):
        if isinstance(r, dict):
            return r
        return {
            "security_id": getattr(r, "security_id", None),
            "event_type": getattr(r, "event_type", None),
            "ex_date": getattr(r, "ex_date", None),
            "record_date": getattr(r, "record_date", None),
            "effective_date": getattr(r, "effective_date", None),
            "ratio": getattr(r, "ratio", None),
            "successor_id": getattr(r, "successor_id", None),
            "details": getattr(r, "details", None),
            "source": getattr(r, "source", None),
            "provider": getattr(r, "provider", None),
            "raw_value": getattr(r, "raw_value", None),
            "fetched_at": getattr(r, "fetched_at", None),
            "verified": getattr(r, "verified", False),
            "verification_status": getattr(r, "verification_status", None),
        }

    def upsert_corporate_actions(self, rows, preserve_verified=True, **kwargs):
        n = 0
        now = _now()
        with self._conn() as c:
            for raw in rows or []:
                r = self._ca_to_dict(raw)
                sid = str(r.get("security_id") or r.get("security") or r.get("symbol") or "").strip().upper()
                et = str(r.get("event_type") or r.get("type") or r.get("action") or "").strip().lower()
                if not sid or not et:
                    continue
                ex = r.get("ex_date", r.get("effective_date", r.get("record_date")))
                ex = str(ex).strip() if ex is not None else None
                record_date = r.get("record_date")
                effective_date = r.get("effective_date", r.get("effective"))
                ratio = r.get("ratio")
                succ = r.get("successor_id", r.get("successor"))
                details = r.get("details")
                if isinstance(details, (dict, list)):
                    details = json.dumps(details)
                source = r.get("source", r.get("provider", "manual"))
                provider = r.get("provider", source)
                raw_value = r.get("raw_value", r.get("raw"))
                fetched_at = r.get("fetched_at", now)
                vstatus = r.get("verification_status", r.get("status"))
                if vstatus is None:
                    vstatus = "verified" if r.get("verified") else "provisional"
                verified = 1 if (r.get("verified", False) or str(vstatus).lower() in ("verified", "manual")) else 0

                exrow = c.execute("SELECT verification_status FROM corporate_actions WHERE security_id=? AND event_type=? AND COALESCE(ex_date,'')=COALESCE(?, '')", (sid, et, ex)).fetchone()
                if exrow and self._should_preserve(exrow["verification_status"], vstatus, preserve_verified):
                    n += 1
                    continue

                c.execute('''INSERT INTO corporate_actions (security_id, event_type, ex_date, record_date, effective_date, ratio, successor_id, details, source, provider, raw_value, fetched_at, imported_at, verified, verification_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(security_id, event_type, ex_date, ratio, successor_id) DO UPDATE SET record_date=excluded.record_date, effective_date=excluded.effective_date, details=excluded.details, source=excluded.source, provider=excluded.provider, raw_value=excluded.raw_value, fetched_at=excluded.fetched_at, imported_at=excluded.imported_at, verified=excluded.verified, verification_status=excluded.verification_status''', (sid, et, ex, record_date, effective_date, ratio, succ, details, source, provider, raw_value, fetched_at, now, verified, vstatus))
                n += 1
            c.commit()
        return n
    def upsert_corporate_action_events(self, rows, preserve_verified=True, **kwargs):
        n = 0
        now = _now()
        with self._conn() as c:
            for raw in rows or []:
                r = raw if isinstance(raw, dict) else self._ca_to_dict(raw)
                sid = str(r.get("security_id") or r.get("security") or r.get("symbol") or "").strip().upper()
                et = str(r.get("event_type") or r.get("type") or r.get("action") or "").strip().lower()
                ex = r.get("ex_date", r.get("effective_date"))
                ex = str(ex).strip() if ex is not None else None
                details = r.get("details")
                if isinstance(details, (dict, list)):
                    details = json.dumps(details)
                source = r.get("source", r.get("provider", "manual"))
                c.execute("INSERT INTO corporate_action_events (security_id, event_type, ex_date, details, source, imported_at) VALUES (?,?,?,?,?,?)", (sid, et, ex, details, source, now))
                n += 1
            c.commit()
        try:
            self.upsert_corporate_actions(rows, preserve_verified=preserve_verified)
        except Exception:
            pass
        return n

    def get_corporate_actions(self, security_id=None, verified_only=False, **kwargs):
        with self._conn() as c:
            q = "SELECT * FROM corporate_actions WHERE 1=1"
            params = []
            if security_id is not None:
                q += " AND security_id=?"
                params.append(str(security_id).strip().upper())
            if verified_only:
                q += " AND (verified=1 OR lower(verification_status) IN ('verified','manual'))"
            q += " ORDER BY COALESCE(ex_date,effective_date,record_date)"
            rows = [dict(r) for r in c.execute(q, params).fetchall()]
        return rows

    def get_corporate_action_events(self, security_id=None, **kwargs):
        with self._conn() as c:
            q = "SELECT * FROM corporate_action_events WHERE 1=1"
            params = []
            if security_id is not None:
                q += " AND security_id=?"
                params.append(str(security_id).strip().upper())
            rows = [dict(r) for r in c.execute(q, params).fetchall()]
        return rows
    def _div_to_dict(self, r):
        if isinstance(r, dict):
            return r
        return {
            "security_id": getattr(r, "security_id", None),
            "ex_date": getattr(r, "ex_date", None),
            "record_date": getattr(r, "record_date", None),
            "payment_date": getattr(r, "payment_date", None),
            "dividend_per_share": getattr(r, "dividend_per_share", getattr(r, "amount", None)),
            "currency": getattr(r, "currency", None),
            "source": getattr(r, "source", None),
            "provider": getattr(r, "provider", None),
            "raw_value": getattr(r, "raw_value", None),
            "fetched_at": getattr(r, "fetched_at", None),
            "verified": getattr(r, "verified", False),
            "verification_status": getattr(r, "verification_status", None),
        }

    def upsert_dividend_events(self, rows, preserve_verified=True, **kwargs):
        return self.upsert_dividends(rows, preserve_verified=preserve_verified, **kwargs)

    def upsert_dividends(self, rows, preserve_verified=True, **kwargs):
        n = 0
        now = _now()
        with self._conn() as c:
            for raw in rows or []:
                r = self._div_to_dict(raw)
                sid = str(r.get("security_id") or r.get("security") or r.get("symbol") or "").strip().upper()
                ex = r.get("ex_date", r.get("exDate"))
                ex = str(ex).strip() if ex is not None else None
                record_date = r.get("record_date")
                payment_date = r.get("payment_date", r.get("paymentDate", r.get("pay_date")))
                dps = r.get("dividend_per_share", r.get("dividend", r.get("amount", r.get("dps"))))
                try:
                    dps = float(dps) if dps is not None else None
                except (TypeError, ValueError):
                    dps = None
                currency = r.get("currency")
                source = r.get("source", r.get("provider", "manual"))
                provider = r.get("provider", source)
                raw_value = r.get("raw_value", r.get("raw", dps))
                try:
                    raw_value = float(raw_value) if raw_value is not None else dps
                except (TypeError, ValueError):
                    raw_value = dps
                fetched_at = r.get("fetched_at", now)
                vstatus = r.get("verification_status", r.get("status"))
                if vstatus is None:
                    vstatus = "verified" if r.get("verified") else "provisional"
                verified = 1 if (r.get("verified", False) or str(vstatus).lower() in ("verified", "manual")) else 0

                exrow = c.execute("SELECT verification_status FROM dividend_events WHERE security_id=? AND COALESCE(ex_date,'')=COALESCE(?, '')", (sid, ex)).fetchone()
                if exrow and self._should_preserve(exrow["verification_status"], vstatus, preserve_verified):
                    n += 1
                    continue

                c.execute('''INSERT INTO dividend_events (security_id, ex_date, record_date, payment_date, dividend_per_share, currency, source, provider, raw_value, fetched_at, imported_at, verified, verification_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(security_id, ex_date, dividend_per_share) DO UPDATE SET record_date=excluded.record_date, payment_date=excluded.payment_date, currency=excluded.currency, source=excluded.source, provider=excluded.provider, raw_value=excluded.raw_value, fetched_at=excluded.fetched_at, imported_at=excluded.imported_at, verified=excluded.verified, verification_status=excluded.verification_status''', (sid, ex, record_date, payment_date, dps, currency, source, provider, raw_value, fetched_at, now, verified, vstatus))
                n += 1
            c.commit()
        return n

    def get_dividend_events(self, security_id=None, verified_only=False, **kwargs):
        with self._conn() as c:
            q = "SELECT * FROM dividend_events WHERE 1=1"
            params = []
            if security_id is not None:
                q += " AND security_id=?"
                params.append(str(security_id).strip().upper())
            if verified_only:
                q += " AND (verified=1 OR lower(verification_status) IN ('verified','manual'))"
            q += " ORDER BY ex_date"
            rows = [dict(r) for r in c.execute(q, params).fetchall()]
        return rows
    def log_sync_run(self, run=None, **kwargs):
        data = {}
        if isinstance(run, dict):
            data.update(run)
        data.update(kwargs)
        with self._conn() as c:
            cur = c.execute('''INSERT INTO market_data_sync_runs (started_at, finished_at, status, provider, scope, security_count, rows_inserted, rows_updated, errors, error_text, details) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (data.get("started_at", data.get("start_time", _now())), data.get("finished_at", data.get("end_time")), data.get("status", "success"), data.get("provider"), data.get("scope"), int(data.get("security_count", data.get("securities_count", 0)) or 0), int(data.get("rows_inserted", data.get("prices_upserted", 0)) or 0), int(data.get("rows_updated", 0) or 0), int(data.get("errors", data.get("error_count", 0)) or 0), data.get("error_text", data.get("error")), (json.dumps(data.get("details")) if isinstance(data.get("details"), (dict, list)) else data.get("details"))))
            rid = cur.lastrowid
            c.commit()
        return int(rid)

    def record_sync_run(self, *args, **kwargs):
        return self.log_sync_run(*args, **kwargs)

    def add_sync_run(self, *args, **kwargs):
        return self.log_sync_run(*args, **kwargs)

    def create_sync_run(self, **kwargs):
        return self.start_sync_run(**kwargs)

    def start_sync_run(self, provider=None, scope=None, security_count=0, status="running", **kwargs):
        data = {"provider": provider, "scope": scope, "security_count": security_count, "status": status, "started_at": _now()}
        data.update(kwargs)
        return self.log_sync_run(data)

    def finish_sync_run(self, run_id, status="success", rows_inserted=0, rows_updated=0, errors=0, error_text=None, finished_at=None, details=None, **kwargs):
        with self._conn() as c:
            c.execute('''UPDATE market_data_sync_runs SET finished_at=?, status=?, rows_inserted=?, rows_updated=?, errors=?, error_text=?, details=COALESCE(?,details) WHERE id=?''', (finished_at or _now(), status, int(rows_inserted or 0), int(rows_updated or 0), int(errors or 0), error_text, (json.dumps(details) if isinstance(details, (dict, list)) else details), run_id))
            c.commit()
        return run_id

    def update_sync_run(self, run_id, **kwargs):
        fields = []
        params = []
        mapping = {"status": "status", "rows_inserted": "rows_inserted", "rows_updated": "rows_updated", "errors": "errors", "error_text": "error_text", "finished_at": "finished_at", "details": "details"}
        for k, col in mapping.items():
            if k in kwargs:
                v = kwargs[k]
                if col == "details" and isinstance(v, (dict, list)):
                    v = json.dumps(v)
                fields.append(f"{col}=?")
                params.append(v)
        if not fields:
            return run_id
        params.append(run_id)
        with self._conn() as c:
            c.execute(f"UPDATE market_data_sync_runs SET {', '.join(fields)} WHERE id=?", params)
            c.commit()
        return run_id

    def get_sync_runs(self, limit=100, **kwargs):
        with self._conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM market_data_sync_runs ORDER BY id DESC LIMIT ?", (int(limit or 100),)).fetchall()]
        return rows

    def list_sync_runs(self, **kwargs):
        return self.get_sync_runs(**kwargs)
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
            with self._conn() as c:
                row = c.execute("SELECT level, trade_date FROM benchmark_history WHERE index_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1", (iid, date)).fetchone()
        if row is None:
            return None
        if not allow_last and row["trade_date"] != date:
            return None
        return float(row["level"])

    def get_benchmark_history(self, index_id=None, start=None, end=None, **kwargs):
        with self._conn() as c:
            q = "SELECT * FROM benchmark_history WHERE 1=1"
            params = []
            if index_id is not None:
                q += " AND index_id=?"
                params.append(str(index_id).strip().upper())
            if start is not None:
                q += " AND trade_date>=?"
                params.append(str(start))
            if end is not None:
                q += " AND trade_date<=?"
                params.append(str(end))
            q += " ORDER BY trade_date"
            rows = [dict(r) for r in c.execute(q, params).fetchall()]
        return rows
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
            idxs2 = [r[0] for r in c.execute("SELECT DISTINCT index_id FROM benchmark_history").fetchall()]
            for i in idxs2:
                if i not in idxs:
                    idxs.append(i)
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
                if cnt == 0:
                    cnt = c.execute("SELECT COUNT(*) FROM benchmark_history WHERE index_id=?", (iid,)).fetchone()[0]
                mn, mx = c.execute("SELECT MIN(trade_date), MAX(trade_date) FROM index_tri WHERE index_id=?", (iid,)).fetchone()
                if mn is None:
                    mn, mx = c.execute("SELECT MIN(trade_date), MAX(trade_date) FROM benchmark_history WHERE index_id=?", (iid,)).fetchone()
                index_coverage[iid] = {"rows": cnt, "from": mn, "to": mx}
        return {"securities": secs, "indexes": idxs, "stock_coverage": coverage, "index_coverage": index_coverage, "anomalies": anomalies}

    # Aliases for common naming variants
    def upsert_symbol_mapping(self, rows, preserve_verified=True, **kwargs):
        return self.upsert_security_symbol_map(rows, preserve_verified=preserve_verified, **kwargs)

    def upsert_symbols(self, rows, preserve_verified=True, **kwargs):
        return self.upsert_security_symbol_map(rows, preserve_verified=preserve_verified, **kwargs)

    def upsert_symbol_maps(self, rows, preserve_verified=True, **kwargs):
        return self.upsert_security_symbol_map(rows, preserve_verified=preserve_verified, **kwargs)

    def get_symbol_mapping(self, security_id=None, provider=None, **kwargs):
        return self.get_symbol_map(security_id=security_id, provider=provider, **kwargs)

    def list_symbols(self, **kwargs):
        return self.get_symbol_map(**kwargs)

    def upsert_corporate_action(self, rows, preserve_verified=True, **kwargs):
        return self.upsert_corporate_actions(rows, preserve_verified=preserve_verified, **kwargs)

    def upsert_dividend_event(self, rows, preserve_verified=True, **kwargs):
        return self.upsert_dividend_events(rows, preserve_verified=preserve_verified, **kwargs)

    def get_dividends(self, security_id=None, verified_only=False, **kwargs):
        return self.get_dividend_events(security_id=security_id, verified_only=verified_only, **kwargs)

    def get_corporate_action(self, security_id=None, verified_only=False, **kwargs):
        return self.get_corporate_actions(security_id=security_id, verified_only=verified_only, **kwargs)

    def upsert_benchmark(self, rows, preserve_verified=True, **kwargs):
        return self.upsert_benchmark_history(rows, preserve_verified=preserve_verified, **kwargs)

    def upsert_sync_run(self, *args, **kwargs):
        return self.log_sync_run(*args, **kwargs)

    def get_sync_run(self, run_id=None, **kwargs):
        runs = self.get_sync_runs(**kwargs)
        if run_id is None:
            return runs[0] if runs else None
        for r in runs:
            if r.get("id") == run_id:
                return r
        return None

    def get_market_data(self, **kwargs):
        return self.get_market_data_quality_report()
    def verify_corporate_action(self, action_id, verified=True, verified_by=None, notes=None):
        with self._conn() as c:
            c.execute("UPDATE corporate_actions SET verified=?, verification_status=?, verified_by=?, verified_at=?, notes=COALESCE(?,notes) WHERE id=?", (1 if verified else 0, "verified" if verified else "rejected", verified_by, _now(), notes, action_id))
            c.commit()
        return action_id

    def upsert_prices(self, rows, **kwargs):
        return self.upsert_stock_prices(rows, **kwargs)

    def upsert_benchmarks(self, rows, **kwargs):
        return self.upsert_benchmark_history(rows, **kwargs)
