"""Tests for the annual sidebar date options and snapshot-price guards."""
from __future__ import annotations

from datetime import date, timedelta

from app.context import (
    default_end_date,
    filter_year_range,
    yearly_end_dates,
    yearly_start_dates,
)
from app.shared import _snapshot_price_usable


def test_filter_year_range_uses_transaction_years():
    txs = [{"trade_date": "2004-05-22"}, {"trade_date": "2026-11-12"}]
    start_year, end_year = filter_year_range(txs)
    assert start_year == 2004
    assert end_year == max(2026, date.today().year)


def test_start_dates_are_first_of_january():
    dates = yearly_start_dates([{"trade_date": "2014-07-01"}])
    assert dates[0] == date(2014, 1, 1)
    assert all(d.month == 1 and d.day == 1 for d in dates)
    assert dates[-1].year == max(date.today().year, 2014)


def test_end_dates_are_year_ends_plus_today():
    dates = yearly_end_dates([{"trade_date": "2014-07-01"}])
    assert date(2014, 12, 31) in dates
    assert date.today() in dates
    assert all(d.month == 12 and d.day == 31 for d in dates if d != date.today())
    assert dates == sorted(dates)


def test_end_dates_never_include_future_year_ends():
    future = date(date.today().year + 1, 6, 1)
    dates = yearly_end_dates([{"trade_date": future.isoformat()}])
    assert all(d <= date.today() for d in dates)
    assert date.today() in dates


def test_empty_transactions_still_produce_options():
    today = date.today()
    assert yearly_start_dates([]) == [date(today.year, 1, 1)]
    assert yearly_end_dates([]) == [today]


def test_default_end_date_uses_year_end_for_older_data():
    assert default_end_date([{"trade_date": "2024-12-30"}]) == date(2024, 12, 31)


def test_default_end_date_uses_today_for_current_year_data():
    assert default_end_date([{"trade_date": date.today().isoformat()}]) == date.today()


def test_default_end_date_without_transactions_is_today():
    assert default_end_date([]) == date.today()


def test_end_dates_accept_settlement_dates_only():
    dates = yearly_end_dates([{"settlement_date": "2014-01-02"}])
    assert date(2014, 12, 31) in dates


def test_snapshot_price_unusable_for_distant_end_date():
    assert _snapshot_price_usable("2015-12-31", "2026-01-01") is False


def test_snapshot_price_usable_for_recent_end_date():
    today = date.today().isoformat()
    assert _snapshot_price_usable(today, today) is True


def test_snapshot_price_usable_assumes_now_when_as_of_unknown():
    assert _snapshot_price_usable(date.today().isoformat(), None) is True


def test_snapshot_price_unusable_without_end_date():
    assert _snapshot_price_usable(None, "2026-01-01") is False


def test_snapshot_gap_boundary_is_45_days():
    end = date(2020, 1, 1)
    assert _snapshot_price_usable(end.isoformat(), (end + timedelta(days=45)).isoformat())
    assert not _snapshot_price_usable(end.isoformat(), (end + timedelta(days=46)).isoformat())
