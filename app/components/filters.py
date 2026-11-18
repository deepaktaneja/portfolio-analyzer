"""Sidebar global-filters component.

Renders date range, asset-type and other contextual filters. Filters are stored
in ``st.session_state`` so they persist across page navigation without reloading
data.

Date filters are annual by design: the start date is always 1 January and the end
date is 31 December (or today for the running year). Restricting periods to year
boundaries keeps price lookups to a handful of dates per year instead of every
trading day, so historical XIRR windows need far less stored price history.
"""
from __future__ import annotations

import streamlit as st

from app.context import (
    ASSET_TYPES,
    Filters,
    default_end_date,
    yearly_end_dates,
    yearly_start_dates,
)


def _keep_valid(key: str, options: list, fallback) -> None:
    """Reset a stored selection that is no longer one of ``options``."""
    if st.session_state.get(key) not in options:
        st.session_state[key] = fallback


def render_sidebar_filters(txs: list[dict]) -> Filters:
    """Render the global filter sidebar and return a populated Filters object."""
    start_options = yearly_start_dates(txs)
    end_options = yearly_end_dates(txs)
    default_start = start_options[0]
    default_end = default_end_date(txs)
    if default_end not in end_options:
        default_end = end_options[-1]

    try:
        from app.context import _xirr_db
        from src.people import PersonStore
        people = PersonStore(_xirr_db()).list_people()
    except Exception:  # noqa: BLE001
        people = []

    with st.sidebar:
        st.header("Filters")

        asset_type = st.radio(
            "Asset type",
            ASSET_TYPES,
            index=ASSET_TYPES.index(st.session_state.get("filter_asset_type", "All")),
            key="filter_asset_type",
            horizontal=False,
        )

        _keep_valid("filter_start_date", start_options, default_start)
        _keep_valid("filter_end_date", end_options, default_end)
        start_date = st.selectbox(
            "Start year",
            start_options,
            format_func=lambda d: f"1 Jan {d.year}",
            key="filter_start_date",
            help="Annual boundaries keep price lookups to year ends.",
        )
        end_date = st.selectbox(
            "End date",
            end_options,
            format_func=lambda d: d.strftime("%d %b %Y"),
            key="filter_end_date",
            help="31 December of a completed year, or today for the running year.",
        )
        if start_date and end_date and start_date > end_date:
            st.warning("Start year is after the end date - widen the range to see cashflows.")

        stock_status = st.selectbox(
            "Stocks",
            ["all", "current", "exited"],
            index=["all", "current", "exited"].index(st.session_state.get("filter_stock_status", "all")),
            key="filter_stock_status",
        )
        dividend_mode = st.selectbox(
            "Dividend view",
            ["gross", "net"],
            index=["gross", "net"].index(st.session_state.get("filter_dividend_mode", "gross")),
            key="filter_dividend_mode",
            help="Net applies an assumed 30% tax on dividends (affects XIRR).",
        )

        options = ["All people"] + [p["name"] for p in people]
        current = st.session_state.get("filter_person", "All people")
        if current not in options:
            current = "All people"
        person_label = st.selectbox(
            "Person", options, index=options.index(current), key="filter_person"
        )

    person_id = None
    person_name = None
    if person_label != "All people":
        rec = next((p for p in people if p["name"] == person_label), None)
        if rec:
            person_id = int(rec["id"])
            person_name = rec["name"]

    return Filters(
        start_date=start_date,
        end_date=end_date,
        asset_type=asset_type,
        stock_status=stock_status,
        dividend_mode=dividend_mode,
        person_id=person_id,
        person_name=person_name,
    )
