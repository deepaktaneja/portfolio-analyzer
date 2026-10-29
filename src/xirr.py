"""XIRR calculation helpers."""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

import pandas as pd
from pyxirr import xirr as _pyxirr_xirr


def to_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    # pandas Timestamp
    try:
        return pd.Timestamp(value).date()
    except Exception:  # noqa: BLE001
        raise ValueError(f"Cannot parse date: {value!r}")


def compute_xirr(cashflows: Iterable[tuple]) -> float | None:
    """cashflows: iterable of (date, amount). Returns XIRR as fraction or None."""
    rows = []
    for d, amt in cashflows:
        try:
            rows.append((to_date(d), float(amt)))
        except (ValueError, TypeError):
            continue
    if len(rows) < 2:
        return None
    df = pd.DataFrame(rows, columns=["date", "amount"])
    df = df.groupby("date", as_index=False)["amount"].sum()
    # Need at least one negative and one positive
    if not (df["amount"].min() < 0 < df["amount"].max()):
        return None
    try:
        result = _pyxirr_xirr(df["date"].tolist(), df["amount"].tolist())
    except Exception:  # noqa: BLE001
        return None
    if result is None:
        return None
    try:
        import math

        if math.isnan(result) or math.isinf(result):
            return None
    except Exception:  # noqa: BLE001
        pass
    return float(result)


def annualize_pct(xirr_value: float | None) -> float | None:
    if xirr_value is None:
        return None
    return round(xirr_value * 100, 2)