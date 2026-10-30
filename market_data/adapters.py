from __future__ import annotations

import os

import pandas as pd

from importers.detection import detect_tabular

STOCK_ALIASES = {
    "date": {"date", "tradedate", "tradingdate", "timestamp"},
    "security_id": {"symbol", "ticker", "isin", "security", "securityid", "code"},
    "close": {"close", "closeprice", "closingprice", "ltp", "lastprice"},
    "exchange": {"exchange", "exch"},
}

INDEX_ALIASES = {
    "date": {"date", "tradedate", "tradingdate", "timestamp"},
    "level": {"level", "close", "value", "tri", "indexlevel", "indexvalue"},
}


def _norm(s):
    return __import__("re").sub(r"[^a-z0-9]", "", str(s or "").lower())

def _detect_columns(df, aliases):
    mapping = {}
    for field, als in aliases.items():
        for col in df.columns:
            n = _norm(col)
            if n in {_norm(a) for a in als} or any(_norm(a) in n or n in _norm(a) for a in als):
                mapping[field] = col
                break
    return mapping

def _read_frame(path):
    dtype = detect_tabular(path)
    if dtype == "xlsx":
        import pandas as pd
        return pd.read_excel(path, engine="openpyxl")
    if dtype == "xls_ole2":
        import pandas as pd
        return pd.read_excel(path, engine="xlrd")
    if dtype == "html":
        import pandas as pd
        tabs = pd.read_html(path)
        return tabs[0] if tabs else pd.DataFrame()
    for sep in ("\t", ","):
        try:
            import pandas as pd
            df = pd.read_csv(path, sep=sep, engine="python")
            if df is not None and df.shape[1] > 1:
                return df
        except Exception:  # noqa: BLE001, S112
            continue
    raise ValueError("could not read")


def import_stock_prices(path, exchange="NSE", source=None):
    from datetime import datetime
    df = _read_frame(path)
    m = _detect_columns(df, STOCK_ALIASES)
    if "date" not in m or "security_id" not in m or "close" not in m:
        raise ValueError("missing required columns")
    rows = []
    warnings = []
    for _, r in df.iterrows():
        try:
            d = str(r[m["date"]]).strip()
            parsed = None
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
                try:
                    parsed = datetime.strptime(d, fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
            if parsed is None:
                warnings.append(f"bad date: {d}")
                continue
            sid = str(r[m["security_id"]]).strip().upper()
            close = float(str(r[m["close"]]).replace(",", ""))
            ex = str(r[m["exchange"]]).strip().upper() if "exchange" in m and pd.notna(r[m["exchange"]]) else exchange
            rows.append({"security_id": sid, "exchange": ex, "trade_date": parsed, "close_price": close, "source": source or os.path.basename(path)})
        except Exception as e:  # noqa: BLE001
            warnings.append(str(e))
    return rows, warnings


def import_index_tri(path, index_id, source=None):
    from datetime import datetime
    df = _read_frame(path)
    m = _detect_columns(df, INDEX_ALIASES)
    if "date" not in m or "level" not in m:
        raise ValueError("missing required columns")
    rows = []
    warnings = []
    for _, r in df.iterrows():
        try:
            d = str(r[m["date"]]).strip()
            parsed = None
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
                try:
                    parsed = datetime.strptime(d, fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
            if parsed is None:
                warnings.append(f"bad date: {d}")
                continue
            level = float(str(r[m["level"]]).replace(",", ""))
            rows.append({"index_id": index_id.strip().upper(), "trade_date": parsed, "level": level, "source": source or os.path.basename(path)})
        except Exception as e:  # noqa: BLE001
            warnings.append(str(e))
    return rows, warnings

