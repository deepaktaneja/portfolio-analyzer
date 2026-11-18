"""Tests for the dependency-free Yahoo chart HTTP provider."""
from __future__ import annotations

import pytest

from market_data.providers import YahooChartProvider

STAMP_1 = 1433116800  # 2015-06-01T00:00:00Z
STAMP_2 = 1433203200  # 2015-06-02T00:00:00Z


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return _Resp(self.payload, self.status)


def _payload():
    return {
        "chart": {
            "result": [{
                "timestamp": [STAMP_1, STAMP_2],
                "indicators": {
                    "quote": [{"close": [100.0, None]}],
                    "adjclose": [{"adjclose": [99.0, 98.5]}],
                },
                "events": {
                    "dividends": {str(STAMP_2): {"amount": 2.5}},
                    "splits": {str(STAMP_2): {"numerator": 2, "denominator": 1}},
                },
            }],
            "error": None,
        }
    }


def test_fetch_prices_parses_chart_rows():
    session = _Session(_payload())
    prov = YahooChartProvider(session=session)
    rows = prov.fetch_prices(
        symbol="ABC.NS", start="2015-06-01", end="2015-06-02", security_id="INE001A01036"
    )
    assert [r["trade_date"] for r in rows] == ["2015-06-01"]
    assert rows[0]["close_price"] == 100.0
    assert rows[0]["adjusted_close"] == 99.0
    assert rows[0]["security_id"] == "INE001A01036"
    assert rows[0]["provider"] == "yahoo_chart"
    call = session.calls[0]
    assert call["url"].endswith("/ABC.NS")
    assert call["params"]["period1"] == STAMP_1
    assert call["params"]["interval"] == "1d"


def test_fetch_prices_filters_out_of_range_dates():
    session = _Session(_payload())
    prov = YahooChartProvider(session=session)
    rows = prov.fetch_prices(symbol="ABC.NS", start="2015-06-02", end="2015-06-30")
    assert rows == []


def test_fetch_prices_raises_on_chart_error():
    payload = {"chart": {"result": None, "error": {"code": "Not Found",
                                                   "description": "No data found"}}}
    prov = YahooChartProvider(session=_Session(payload))
    with pytest.raises(RuntimeError, match="No data found"):
        prov.fetch_prices(symbol="NOPE.NS")


def test_fetch_prices_retries_then_raises_on_http_error():
    session = _Session({}, status=429)
    prov = YahooChartProvider(session=session, retries=3)
    with pytest.raises(RuntimeError, match="http 429"):
        prov.fetch_prices(symbol="ABC.NS")
    assert len(session.calls) == 3


def test_fetch_dividends_and_splits_use_chart_events():
    prov = YahooChartProvider(session=_Session(_payload()))
    divs = prov.fetch_dividends(symbol="ABC.NS", start="2015-06-01", end="2015-06-02",
                                security_id="INE001A01036")
    assert len(divs) == 1
    assert divs[0]["dividend_per_share"] == 2.5
    assert divs[0]["verification_status"] == "provisional"

    splits = prov.fetch_splits(symbol="ABC.NS", start="2015-06-01", end="2015-06-02",
                               security_id="INE001A01036")
    assert len(splits) == 1
    assert splits[0]["ratio"] == 2.0
    assert splits[0]["event_type"] == "split"


def test_requires_a_ticker():
    prov = YahooChartProvider(session=_Session(_payload()))
    assert prov.fetch_prices() == []
