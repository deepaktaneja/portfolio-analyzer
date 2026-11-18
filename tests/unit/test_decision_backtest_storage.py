"""Tests for the backtest engine & decision storage (Stage 8)."""
from __future__ import annotations

from decision.backtest import (
    evaluate_signals,
    filter_signals_until,
    forward_return,
    price_as_of,
    run_backtest,
)
from decision.engine import DecisionEngine
from decision.storage import (
    acknowledge_recommendation,
    list_history,
    list_recent_recommendations,
    record_decision,
    save_backtest,
    save_recommendation,
    save_signals,
)
from models.decision import get_decision_session

# Declining series: TRIM before decline should be a "hit".
DECLINE = [
    ("2024-01-01", 100), ("2024-02-01", 95), ("2024-03-01", 90),
    ("2024-04-01", 85), ("2024-05-01", 80), ("2024-06-01", 75), ("2024-07-01", 70),
]


def test_price_as_of_point_in_time():
    series = [(__import__("datetime").date(2024, 1, 1), 100), (__import__("datetime").date(2024, 2, 1), 110)]
    d = __import__("datetime").date(2024, 1, 15)
    assert price_as_of(series, d) == 100  # never uses the 2024-02-01 bar


def test_forward_return():
    from datetime import date
    series = [(date(2024, 1, 1), 100), (date(2024, 4, 1), 90)]
    r = forward_return(series, date(2024, 1, 1), 95)  # ~2024-04-05 includes the 2024-04-01 bar
    assert r is not None and abs(r - (-0.10)) < 1e-9


def test_filter_signals_until_guard():
    events = [{"symbol": "A", "date": "2024-01-01", "action": "TRIM_REVIEW"},
              {"symbol": "A", "date": "2024-12-01", "action": "EXIT_REVIEW"}]
    kept = filter_signals_until(events, "2024-06-01")
    assert len(kept) == 1 and kept[0]["date"] == "2024-01-01"


def test_evaluate_signals_hit_and_unvalidatable():
    price_data = {"AAA": DECLINE}
    signals = [
        {"symbol": "AAA", "date": "2024-01-01", "action": "TRIM_REVIEW"},
        {"symbol": "BBB", "date": "2024-01-01", "action": "EXIT_REVIEW"},  # no data
    ]
    res = evaluate_signals(price_data, signals)
    assert res["signals_evaluated"] == 1  # only AAA has price data; BBB goes to unvalidatable
    assert res["signals_validatable"] == 1
    assert len(res["unvalidatable"]) == 1
    assert res["hit_rate"] == 1.0  # decline after TRIM -> hit
    assert res["false_positive_rate"] == 0.0


def test_run_backtest_wrapper():
    res = run_backtest({"AAA": DECLINE}, [{"symbol": "AAA", "date": "2024-01-01", "action": "EXIT_REVIEW"}])
    assert "buy_hold_return" in res
    assert res["signal_value_proxy"] is not None


# ---------------------------------------------------------------------------
# Storage round-trip (isolated temp DB)
# ---------------------------------------------------------------------------

def test_storage_roundtrip(tmp_path):
    s = get_decision_session(db_url=f"sqlite:///{tmp_path / 'dec.sqlite3'}")
    try:
        eng = DecisionEngine()
        rec = eng.evaluate_equity({"security_id": "AAA", "weight_pct": 20.0}, {})
        n = save_signals(rec["signals"], security_id="AAA", session=s)
        assert n >= 1
        rid = save_recommendation(rec, session=s)
        assert rid >= 1
        hid = record_decision(rid, "AAA", "HELD", note="watch", session=s)
        assert hid >= 1
        assert len(list_recent_recommendations(session=s)) >= 1
        assert len(list_history(session=s)) >= 1
        bid = save_backtest("run1", {"p": 1}, {"hit_rate": 0.5}, session=s)
        assert bid >= 1
        assert acknowledge_recommendation(rid, session=s) is True
    finally:
        s.close()
