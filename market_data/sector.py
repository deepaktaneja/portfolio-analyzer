"""Sector enrichment and manual symbol mapping (yfinance)."""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import UTC, datetime

try:
    import yfinance as _yf
except Exception:  # noqa: BLE001
    _yf = None

yfinance = _yf

MARKET_DB = os.environ.get("ICICI_MARKET_DATA_DB_PATH", "data/market_data.sqlite3")
XIRR_DB = os.environ.get("ICICI_XIRR_DB_PATH", "data/xirr.sqlite3").replace("sqlite:///", "", 1)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(XIRR_DB)
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE IF NOT EXISTS sector_mapping ("
        "isin TEXT PRIMARY KEY, sector TEXT, industry TEXT, symbol TEXT, "
        "exchange TEXT, provider TEXT, fetched_at TEXT, verified INTEGER NOT NULL DEFAULT 0)"
    )
    con.commit()
    return con


def get_symbol_mappings(isins) -> dict[str, dict]:
    """Return isin -> {symbol, exchange, provider} from security_symbol_map."""
    from market_data.repository import MarketDataStore
    md = MarketDataStore(MARKET_DB)
    out: dict[str, dict] = {}
    for isin in isins:
        isin = str(isin).strip().upper()
        if not isin:
            continue
        try:
            rows = md.get_symbol_map(security_id=isin)
        except Exception:  # noqa: BLE001
            rows = []
        if not rows:
            continue
        rows.sort(key=lambda r: (0 if str(r.get("provider")) == "manual" else 1, 0 if r.get("verified") else 1))
        r = rows[0]
        out[isin] = {
            "symbol": r.get("symbol") or r.get("provider_symbol"),
            "exchange": r.get("exchange"),
            "provider": r.get("provider"),
        }
    return out


def upsert_symbol_mapping(isin: str, symbol: str, exchange: str = "NSE") -> str:
    """Save a manual ISIN -> yfinance symbol mapping (adds .NS/.BO if missing)."""
    isin = str(isin).strip().upper()
    sym = str(symbol).strip().upper()
    if not isin or not sym:
        raise ValueError("ISIN and symbol are required")
    ex = (exchange or "NSE").strip().upper()
    if not sym.endswith((".NS", ".BO")):
        sym = sym + (".BO" if ex == "BSE" else ".NS")
    from market_data.repository import MarketDataStore
    md = MarketDataStore(MARKET_DB)
    md.upsert_security_symbol_map([
        {"security_id": isin, "symbol": sym, "provider_symbol": sym, "exchange": ex,
         "provider": "manual", "source": "manual", "verification_status": "manual", "verified": True}
    ])
    return sym


def fetch_sector_from_yfinance(symbol: str) -> dict:
    if yfinance is None:
        return {"error": "yfinance_not_installed"}
    try:
        ticker = yfinance.Ticker(symbol)
        info = None
        try:
            info = ticker.info
        except Exception:  # noqa: BLE001
            info = None
        if not info and hasattr(ticker, "get_info"):
            try:
                info = ticker.get_info()
            except Exception:  # noqa: BLE001
                info = None
        if isinstance(info, dict):
            sector = info.get("sector")
            industry = info.get("industry")
            if sector or industry:
                return {"sector": sector, "industry": industry}
        return {"error": "no_sector"}
    except Exception as e:  # noqa: BLE001
        return {"error": "fetch_failed: " + str(e)}


def enrich_sectors_for_isins(isins, force: bool = False, delay: float = 0.3, max_calls: int = 200) -> dict[str, dict]:
    """Fetch sector/industry via yfinance for ISINs that have a symbol mapping."""
    results: dict[str, dict] = {}
    maps = get_symbol_mappings(isins)
    cached = get_sector_mapping(isins)
    calls = 0
    for isin in isins:
        isin = str(isin).strip().upper()
        if not isin:
            continue
        if not force and (cached.get(isin) or {}).get("sector"):
            continue
        m = maps.get(isin)
        if not m or not m.get("symbol"):
            results[isin] = {"error": "no_symbol_mapping"}
            continue
        if calls >= max_calls:
            results[isin] = {"error": "batch_limit_reached"}
            continue
        res = fetch_sector_from_yfinance(m["symbol"])
        if res.get("sector"):
            res["symbol"] = m["symbol"]
            res["exchange"] = m.get("exchange")
            res["provider"] = "yfinance"
        results[isin] = res
        calls += 1
        time.sleep(delay)
    return results


def upsert_sector_mapping(results) -> int:
    con = _connect()
    count = 0
    try:
        with con:
            for isin, data in results.items():
                if data.get("error"):
                    continue
                con.execute(
                    "INSERT INTO sector_mapping (isin, sector, industry, symbol, exchange, provider, fetched_at, verified) "
                    "VALUES (?,?,?,?,?,?,?,1) "
                    "ON CONFLICT(isin) DO UPDATE SET sector=excluded.sector, industry=excluded.industry, "
                    "symbol=excluded.symbol, exchange=excluded.exchange, provider=excluded.provider, "
                    "fetched_at=excluded.fetched_at, verified=excluded.verified",
                    (str(isin).strip().upper(), data.get("sector"), data.get("industry"),
                     data.get("symbol"), data.get("exchange"), data.get("provider"), _now()),
                )
                count += 1
    finally:
        con.close()
    return count


def get_sector_mapping(isins=None) -> dict[str, dict]:
    con = _connect()
    try:
        if isins:
            clean = [str(i).strip().upper() for i in isins if str(i).strip()]
            if not clean:
                return {}
            ph = ",".join(["?"] * len(clean))
            rows = con.execute("SELECT isin, sector, industry, symbol, exchange, provider FROM sector_mapping WHERE isin IN (" + ph + ")", tuple(clean)).fetchall()
        else:
            rows = con.execute("SELECT isin, sector, industry, symbol, exchange, provider FROM sector_mapping").fetchall()
        return {r["isin"]: dict(r) for r in rows}
    finally:
        con.close()


def enrich_holdings_with_sectors(df) -> None:
    """Fill sector/industry columns from the cached mapping (does not overwrite with None)."""
    if df is None or getattr(df, "empty", True) or "isin" not in df.columns:
        return
    isins = [str(x).strip().upper() for x in df["isin"].dropna().unique().tolist()]
    if not isins:
        return
    mapping = get_sector_mapping(isins)
    if not mapping:
        return
    secs = list(df["sector"]) if "sector" in df.columns else [None] * len(df)
    inds = list(df["industry"]) if "industry" in df.columns else [None] * len(df)
    for i, val in enumerate(df["isin"]):
        m = mapping.get(str(val).strip().upper())
        if not m:
            continue
        if m.get("sector"):
            secs[i] = m["sector"]
        if m.get("industry"):
            inds[i] = m["industry"]
    df["sector"] = secs
    df["industry"] = inds


def holding_status(hdf) -> list[dict]:
    """Return per-holding mapping status for the UI."""
    if hdf is None or getattr(hdf, "empty", True) or "isin" not in hdf.columns:
        return []
    isins = [str(x).strip().upper() for x in hdf["isin"].dropna().unique().tolist()]
    maps = get_symbol_mappings(isins)
    cached = get_sector_mapping(isins)
    out = []
    for _, r in hdf.iterrows():
        isin = str(r.get("isin") or "").strip().upper()
        m = maps.get(isin) or {}
        c = cached.get(isin) or {}
        if c.get("sector"):
            status = "ready"
        elif m.get("symbol"):
            status = "mapped"
        else:
            status = "needs symbol"
        out.append({"isin": isin, "symbol": r.get("symbol"), "mapped_symbol": m.get("symbol"), "sector": c.get("sector"), "industry": c.get("industry"), "status": status})
    return out


def upsert_sector_override(isin: str, sector: str, industry: str | None = None) -> None:
    """Manually set sector/industry for an ISIN (used when yfinance cannot confirm)."""
    isin = str(isin).strip().upper()
    sec = str(sector).strip()
    if not isin or not sec:
        raise ValueError("ISIN and sector are required")
    con = _connect()
    try:
        with con:
            con.execute(
                "INSERT INTO sector_mapping (isin, sector, industry, symbol, exchange, provider, fetched_at, verified) "
                "VALUES (?,?,?,NULL,NULL,?,?,1) "
                "ON CONFLICT(isin) DO UPDATE SET sector=excluded.sector, industry=excluded.industry, "
                "provider=excluded.provider, fetched_at=excluded.fetched_at, verified=excluded.verified",
                (isin, sec, (industry or None), "manual", _now()),
            )
    finally:
        con.close()
