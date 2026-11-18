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


def _ts_to_date(ts):
    """Unix seconds -> ``YYYY-MM-DD`` in UTC (Yahoo stamps align with the trade day)."""
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


def _retry_after(resp):
    """Honour a ``Retry-After`` header when the server sends one (seconds)."""
    try:
        wait = float(resp.headers.get("Retry-After") or 0)
    except (AttributeError, TypeError, ValueError):
        return None
    return min(wait, 30.0) if wait > 0 else None


class YahooChartProvider:
    """Yahoo Finance chart API over plain HTTP (no ``yfinance`` dependency).

    Mirrors :class:`YFinanceProvider`'s row contract so it can act as a drop-in
    fallback when the yfinance library fails, is unavailable or is rate limited.
    """

    name = "yahoo_chart"
    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"

    def __init__(self, timeout=20, retries=3, session=None, min_interval=None, **kwargs):
        self.timeout = timeout
        self.retries = max(1, int(retries or 1))
        self._session = session
        if min_interval is None:
            try:
                min_interval = float(os.environ.get("ICICI_PRICE_FETCH_MIN_INTERVAL", "1.0"))
            except (TypeError, ValueError):
                min_interval = 1.0
        self.min_interval = max(0.0, float(min_interval))
        self._last_call = 0.0

    def _http(self):
        if self._session is not None:
            return self._session
        import requests

        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
        })
        return session

    @staticmethod
    def _epoch(value):
        if value is None:
            return None
        try:
            parsed = datetime.strptime(str(value)[:10], "%Y-%m-%d").replace(tzinfo=UTC)
            return int(parsed.timestamp())
        except ValueError:
            return None

    def _params(self, start, end):
        params = {"interval": "1d", "events": "div,splits"}
        p1, p2 = self._epoch(start), self._epoch(end)
        if p1 is not None:
            params["period1"] = p1
        if p2 is not None:
            params["period2"] = p2
        if p1 is None and p2 is None:
            params["range"] = "1y"
        return params

    def _throttle(self):
        """Space requests out so the unauthenticated endpoint does not rate limit us."""
        if self.min_interval > 0 and self._last_call:
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
        self._last_call = time.time()

    def _get_json(self, ticker, params):
        url = self.BASE_URL + ticker
        last_error = None
        for attempt in range(self.retries):
            self._throttle()
            try:
                resp = self._http().get(url, params=params, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                time.sleep(1.5 * (attempt + 1))
                continue
            status = getattr(resp, "status_code", 200)
            if status == 200:
                try:
                    return resp.json()
                except Exception as exc:  # noqa: BLE001
                    last_error = f"bad json: {exc}"
                    continue
            last_error = f"http {status}"
            if status in (429, 500, 502, 503, 504):
                time.sleep(_retry_after(resp) or 2.0 * (attempt + 1))
                continue
            break
        raise RuntimeError(last_error or "request failed")

    def fetch(self, *args, **kwargs):
        return self.fetch_prices(*args, **kwargs)

    def fetch_prices(self, symbol=None, start=None, end=None, security_id=None,
                     exchange=None, **kwargs):
        ticker = symbol or kwargs.get("ticker") or kwargs.get("provider_symbol") or security_id
        if not ticker:
            return []
        sid = security_id or symbol or ticker
        sid = str(sid).strip().upper() if sid is not None else None
        ex = str(exchange or kwargs.get("exchange") or "NSE").upper()
        data = self._get_json(str(ticker).strip(), self._params(start, end))
        return self._parse(data, sid, ex, start, end)

    @staticmethod
    def _chart_result(data):
        """Return the first ``chart.result`` entry, raising on a chart error."""
        chart = (data or {}).get("chart") or {}
        error = chart.get("error")
        if error:
            detail = error.get("description") if isinstance(error, dict) else error
            raise RuntimeError(str(detail or "chart error"))
        results = chart.get("result") or []
        return (results[0] or {}) if results else {}

    @staticmethod
    def _split_events(events):
        """Split the ``events`` block into ``(dividends, splits)`` maps keyed by stamp."""
        dividends: dict[int, float] = {}
        for ts, ev in (events.get("dividends") or {}).items():
            try:
                dividends[int(ts)] = float(ev.get("amount"))
            except (TypeError, ValueError, AttributeError):
                continue
        splits: dict[int, float | None] = {}
        for ts, ev in (events.get("splits") or {}).items():
            try:
                denominator = float(ev.get("denominator") or 0)
                splits[int(ts)] = float(ev.get("numerator")) / denominator if denominator else None
            except (TypeError, ValueError, AttributeError):
                continue
        return dividends, splits

    def _chart_events(self, symbol=None, start=None, end=None, security_id=None, **kwargs):
        """One chart request -> ``(sid, dividends, splits)`` (close-independent)."""
        ticker = symbol or kwargs.get("ticker") or kwargs.get("provider_symbol") or security_id
        if not ticker:
            return None, {}, {}
        sid = str(security_id or symbol or ticker).strip().upper()
        data = self._get_json(str(ticker).strip(), self._params(start, end))
        result = self._chart_result(data)
        dividends, splits = self._split_events(result.get("events") or {})
        return sid, dividends, splits

    def _parse(self, data, sid, exchange, start, end):
        result = self._chart_result(data)
        if not result:
            return []
        stamps = result.get("timestamp") or []
        indicators = result.get("indicators") or {}
        quote = (indicators.get("quote") or [{}])[0] or {}
        closes = quote.get("close") or []
        adj_block = (indicators.get("adjclose") or [{}])[0] or {}
        adj_closes = adj_block.get("adjclose") or []
        dividends, splits = self._split_events(result.get("events") or {})
        start_key = str(start)[:10] if start else None
        end_key = str(end)[:10] if end else None
        rows = []
        for i, ts in enumerate(stamps):
            close = closes[i] if i < len(closes) else None
            try:
                close = float(close)
            except (TypeError, ValueError):
                continue
            trade_date = _ts_to_date(ts)
            if not trade_date:
                continue
            if start_key and trade_date < start_key:
                continue
            if end_key and trade_date > end_key:
                continue
            adj = adj_closes[i] if i < len(adj_closes) else None
            try:
                adj = float(adj) if adj is not None else None
            except (TypeError, ValueError):
                adj = None
            stamp = int(ts)
            rows.append({
                "security_id": sid,
                "exchange": exchange,
                "trade_date": trade_date,
                "close_price": close,
                "raw_value": close,
                "raw_close": close,
                "adjusted_close": adj,
                "provider": self.name,
                "source": self.name,
                "fetched_at": _now(),
                "verification_status": "unverified",
                "dividend": dividends.get(stamp),
                "split": splits.get(stamp),
            })
        return rows

    def fetch_dividends(self, symbol=None, start=None, end=None, security_id=None, **kwargs):
        sid, dividends, _ = self._chart_events(
            symbol=symbol, start=start, end=end, security_id=security_id, **kwargs
        )
        rows = []
        for stamp, value in sorted(dividends.items()):
            trade_date = _ts_to_date(stamp)
            if not trade_date or value is None or float(value) <= 0:
                continue
            if start and trade_date < str(start)[:10]:
                continue
            if end and trade_date > str(end)[:10]:
                continue
            rows.append({
                "security_id": sid,
                "ex_date": trade_date,
                "dividend_per_share": float(value),
                "source": self.name,
                "provider": self.name,
                "fetched_at": _now(),
                "verification_status": "provisional",
                "raw_value": float(value),
            })
        return rows

    def fetch_splits(self, symbol=None, start=None, end=None, security_id=None, **kwargs):
        sid, _, splits = self._chart_events(
            symbol=symbol, start=start, end=end, security_id=security_id, **kwargs
        )
        rows = []
        for stamp, value in sorted(splits.items()):
            trade_date = _ts_to_date(stamp)
            if not trade_date or not value:
                continue
            if start and trade_date < str(start)[:10]:
                continue
            if end and trade_date > str(end)[:10]:
                continue
            rows.append({
                "security_id": sid,
                "event_type": "split",
                "ex_date": trade_date,
                "ratio": float(value),
                "source": self.name,
                "provider": self.name,
                "fetched_at": _now(),
                "verification_status": "provisional",
                "raw_value": float(value),
            })
        return rows

    def fetch_actions(self, symbol=None, start=None, end=None, security_id=None, **kwargs):
        return self.fetch_splits(
            symbol=symbol, start=start, end=end, security_id=security_id, **kwargs
        )

    def fetch_corporate_actions(self, symbol=None, start=None, end=None, security_id=None,
                                **kwargs):
        return self.fetch_actions(
            symbol=symbol, start=start, end=end, security_id=security_id, **kwargs
        )

    def fetch_benchmark(self, symbol=None, start=None, end=None, security_id=None, **kwargs):
        sym = symbol or kwargs.get("ticker") or "^NSEI"
        out = []
        for r in self.fetch_prices(
            symbol=sym, start=start, end=end, security_id=security_id or sym, **kwargs
        ):
            rr = dict(r)
            rr["index_id"] = str(security_id or sym).strip().upper()
            rr["level"] = r.get("close_price")
            out.append(rr)
        return out


YahooChartHttpProvider = YahooChartProvider
YahooFinanceProvider = YFinanceProvider

CsvProvider = ManualCsvProvider
ManualProvider = ManualCsvProvider

YahooProvider = YFinanceProvider
YfProvider = YFinanceProvider
MarketDataPriceProvider = MarketDataProvider
