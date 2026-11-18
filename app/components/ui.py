"""Small reusable presentational helpers shared by all pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def section(title: str, description: str | None = None) -> None:
    """Render a consistent section header."""
    st.subheader(title)
    if description:
        st.caption(description)


def metric_row(items: list[tuple[str, str]], columns: int | None = None) -> None:
    """Render a row of metric cards. ``items`` is a list of (label, value)."""
    n = columns or max(1, len(items))
    cols = st.columns(n)
    for i, (label, value) in enumerate(items):
        with cols[i % n]:
            st.metric(label, value)


def safe_dataframe(df, *, key: str | None = None, empty_message: str | None = None) -> None:
    """Render a dataframe if non-empty, otherwise show an info/empty message."""
    try:
        is_empty = df is None or (isinstance(df, pd.DataFrame) and df.empty)
    except Exception:  # noqa: BLE001
        is_empty = df is None
    if is_empty:
        if empty_message:
            st.info(empty_message)
        return
    st.dataframe(df, hide_index=True, width="stretch", key=key)


def data_freshness_caption(label: str, timestamp: str | None) -> None:
    """Show a small data-freshness caption (e.g. last sync / as-of date)."""
    if timestamp:
        st.caption(f"🕒 {label}: {timestamp}")
    else:
        st.caption(f"🕒 {label}: not available")


def missing_data_warning(message: str) -> None:
    """Standardised missing-data warning."""
    st.warning(f"⚠️ {message}")


def estimated_xirr_note(reason: str | None) -> None:
    """Standardised note for estimated / unavailable XIRR."""
    if reason:
        st.warning(f"Estimated XIRR — {reason}")
