"""Tests for the persistent Stage 4 walk-forward backtest cache."""


def test_backtest_cache_roundtrip_and_freshness(tmp_path):
    from performance.stage4_backtest import (
        backtest_cache_key,
        load_backtest_result,
        save_backtest_result,
    )

    result = {"scorecard": {"final_net_value": 123.0}, "daily_curve": []}
    signature = "price-signature-v1"

    path = save_backtest_result(
        result,
        cache_dir=tmp_path,
        person_id=42,
        years=5,
        compare_watchlist=True,
        include_closed=False,
        data_signature=signature,
        saved_at="2026-11-22T12:15:00+05:30",
    )
    expected_key = backtest_cache_key(
        person_id=42, years=5, compare_watchlist=True, include_closed=False
    )
    assert path == tmp_path / f"backtest_{expected_key}.json"

    loaded = load_backtest_result(
        tmp_path,
        person_id=42,
        years=5,
        compare_watchlist=True,
        include_closed=False,
        data_signature=signature,
    )
    assert loaded == result

    stale = load_backtest_result(
        tmp_path,
        person_id=42,
        years=5,
        compare_watchlist=True,
        include_closed=False,
        data_signature="price-signature-v2",
    )
    assert stale is None


def test_backtest_cache_corrupt_file_falls_back(tmp_path):
    from performance.stage4_backtest import load_backtest_result, save_backtest_result

    result = {"scorecard": {"final_net_value": 456.0}}
    save_backtest_result(
        result,
        cache_dir=tmp_path,
        person_id=7,
        years=3,
        compare_watchlist=False,
        include_closed=True,
        data_signature="price-signature-v3",
    )
    cache_file = next(tmp_path.glob("backtest_*.json"))
    cache_file.write_text("{not valid json", encoding="utf-8")

    loaded = load_backtest_result(
        tmp_path,
        person_id=7,
        years=3,
        compare_watchlist=False,
        include_closed=True,
        data_signature="price-signature-v3",
    )
    assert loaded is None
