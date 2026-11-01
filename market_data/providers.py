from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

try:
    import yfinance as _yf
except Exception:
    _yf = None

yfinance = _yf
yf = _yf


def _now() -> str:
    return datetime.now(UTC).isoformat()


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str

    def fetch_prices(self, symbol=None, start=None, end=None, **kwargs) -> list[dict]: ...


DataProvider = MarketDataProvider
PriceProvider = MarketDataProvider


class ManualCsvProvider:
    def __init__(self, path=None, csv_path=None, file_path=None, directory=None, source="manual_csv", **kwargs):
        self.path = path or csv_path or file_path
        self.directory = directory
        self.source = source
        self.name = "manual_csv"

    def _paths(self):
        import glob
        paths = []
        if self.path:
            paths.append(self.path)
        if self.directory:
            for pat in ("*.csv", "*.xlsx", "*.xls"):
                paths.extend(glob.glob(os.path.join(self.directory, pat)))
        return paths

    def fetch(self, *args, **kwargs):
        return self.fetch_prices(*args, **kwargs)

    def fetch_prices(self, symbol=None, start=None, end=None, security_id=None, exchange=None, **kwargs):
        from market_data.adapters import import_stock_prices
        rows = []
        sid_filter = security_id or symbol
        sid_filter = str(sid_filter).strip().upper() if sid_filter is not None else None
        ex = exchange or kwargs.get("exchange") or "NSE"
        for p in self._paths():
            try:
                parsed, _ = import_stock_prices(p, exchange=ex, source=self.source)
            except Exception:
                continue
            for r in parsed:
                if sid_filter and r.get("security_id") != sid_filter:
                    continue
                td = r.get("trade_date")
                if start and td < str(start):
                    continue
                if end and td > str(end):
                    continue
                r = dict(r)
                r.setdefault("provider", "manual_csv")
                r.setdefault("fetched_at", _now())
                r.setdefault("verification_status", "unverified")
                r.setdefault("raw_value", r.get("close_price"))
                rows.append(r)
        return rows

    def fetch_actions(self, *args, **kwargs):
        return []

    def fetch_splits(self, *args, **kwargs):
        return []

    def fetch_dividends(self, *args, **kwargs):
        return []

    def fetch_corporate_actions(self, *args, **kwargs):
        return []

    def fetch_benchmark(self, *args, **kwargs):
        return self.fetch_prices(*args, **kwargs)


class YFinanceProvider:
    def __init__(self, timeout=30, retries=3, auto_adjust=False, actions=True, **kwargs):
        self.timeout = timeout
        self.retries = retries
        self.auto_adjust = auto_adjust
        self.actions = actions
        self.name = "yfinance"

    def _yf(self):
        if yfinance is None:
            raise RuntimeError("yfinance is not installed")
        return yfinance

    def _ticker(self, symbol):
        return self._yf().Ticker(symbol)

    def fetch(self, *args, **kwargs):
        return self.fetch_prices(*args, **kwargs)

    def fetch_prices(self, symbol=None, start=None, end=None, security_id=None, exchange=None, **kwargs):
        ticker = symbol or kwargs.get("ticker") or kwargs.get("provider_symbol") or security_id
        t = self._ticker(ticker)
        df = t.history(start=start, end=end, auto_adjust=self.auto_adjust, actions=self.actions, timeout=self.timeout)
        rows = []
        sid = security_id or symbol or ticker
        sid = str(sid).strip().upper() if sid is not None else None
        ex = exchange or kwargs.get("exchange") or "NSE"
        try:
            for idx, row in df.iterrows():
                td = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                close = row.get("Close")
                try:
                    close = float(close)
                except (TypeError, ValueError):
                    continue
                adj = row.get("Adj Close", None)
                try:
                    adj = float(adj) if adj is not None else None
                except (TypeError, ValueError):
                    adj = None
                rows.append({
                    "security_id": sid,
                    "exchange": str(ex).upper(),
                    "trade_date": td,
                    "close_price": close,
                    "raw_value": close,
                    "raw_close": close,
                    "adjusted_close": adj,
                    "provider": "yfinance",
                    "fetched_at": _now(),
                    "verification_status": "unverified",
                    "dividend": row.get("Dividends"),
                    "split": row.get("Stock Splits"),
                })
        except Exception:
            pass
        return rows

    def fetch_dividends(self, symbol=None, start=None, end=None, security_id=None, **kwargs):
        ticker = symbol or kwargs.get("ticker") or security_id
        t = self._ticker(ticker)
        rows = []
        sid = security_id or symbol or ticker
        sid = str(sid).strip().upper() if sid is not None else None
        try:
            df = getattr(t, "dividends", None)
            if df is not None and len(df) > 0:
                for idx, val in df.items():
                    td = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                    if start and td < str(start):
                        continue
                    if end and td > str(end):
                        continue
                    rows.append({
                        "security_id": sid,
                        "ex_date": td,
                        "dividend_per_share": float(val),
                        "source": "yfinance",
                        "provider": "yfinance",
                        "fetched_at": _now(),
                        "verification_status": "provisional",
                        "raw_value": float(val),
                    })
        except Exception:
            pass
        if not rows:
            try:
                hist = self.fetch_prices(symbol=symbol, start=start, end=end, security_id=security_id, **kwargs)
                for r in hist:
                    val = r.get("dividend")
                    try:
                        val = float(val)
                    except (TypeError, ValueError):
                        continue
                    if val and val != 0.0:
                        td = r.get("trade_date")
                        if start and td < str(start):
                            continue
                        if end and td > str(end):
                            continue
                        rows.append({
                            "security_id": sid,
                            "ex_date": td,
                            "dividend_per_share": val,
                            "source": "yfinance",
                            "provider": "yfinance",
                            "fetched_at": _now(),
                            "verification_status": "provisional",
                            "raw_value": val,
                        })
            except Exception:
                pass
        return rows

    def fetch_splits(self, symbol=None, start=None, end=None, security_id=None, **kwargs):
        ticker = symbol or kwargs.get("ticker") or security_id
        t = self._ticker(ticker)
        rows = []
        sid = security_id or symbol or ticker
        sid = str(sid).strip().upper() if sid is not None else None
        try:
            df = getattr(t, "splits", None)
            if df is not None and len(df) > 0:
                for idx, val in df.items():
                    td = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                    if start and td < str(start):
                        continue
                    if end and td > str(end):
                        continue
                    rows.append({
                        "security_id": sid,
                        "event_type": "split",
                        "ex_date": td,
                        "ratio": float(val),
                        "source": "yfinance",
                        "provider": "yfinance",
                        "fetched_at": _now(),
                        "verification_status": "provisional",
                        "raw_value": float(val),
                    })
        except Exception:
            pass
        if not rows:
            try:
                hist = self.fetch_prices(symbol=symbol, start=start, end=end, security_id=security_id, **kwargs)
                for r in hist:
                    val = r.get("split")
                    try:
                        val = float(val)
                    except (TypeError, ValueError):
                        continue
                    if val and val != 0.0:
                        td = r.get("trade_date")
                        if start and td < str(start):
                            continue
                        if end and td > str(end):
                            continue
                        rows.append({
                            "security_id": sid,
                            "event_type": "split",
                            "ex_date": td,
                            "ratio": val,
                            "source": "yfinance",
                            "provider": "yfinance",
                            "fetched_at": _now(),
                            "verification_status": "provisional",
                            "raw_value": val,
                        })
            except Exception:
                pass
        return rows

    def fetch_actions(self, symbol=None, start=None, end=None, security_id=None, **kwargs):
        return self.fetch_splits(symbol=symbol, start=start, end=end, security_id=security_id, **kwargs)

    def fetch_corporate_actions(self, symbol=None, start=None, end=None, security_id=None, **kwargs):
        return self.fetch_actions(symbol=symbol, start=start, end=end, security_id=security_id, **kwargs)

    def fetch_benchmark(self, symbol=None, start=None, end=None, security_id=None, **kwargs):
        sym = symbol or kwargs.get("ticker") or "^NSEI"
        rows = self.fetch_prices(symbol=sym, start=start, end=end, security_id=security_id or sym, **kwargs)
        out = []
        for r in rows:
            rr = dict(r)
            rr["index_id"] = str(security_id or sym).strip().upper()
            rr["level"] = r.get("close_price")
            rr["trade_date"] = r.get("trade_date")
            out.append(rr)
        return out


YahooFinanceProvider = YFinanceProvider

CsvProvider = ManualCsvProvider
ManualProvider = ManualCsvProvider

YahooProvider = YFinanceProvider
YfProvider = YFinanceProvider
MarketDataPriceProvider = MarketDataProvider
