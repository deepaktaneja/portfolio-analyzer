from __future__ import annotations

import datetime as _dt
from typing import Any


def _backtest_report_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return bool(value)

def _backtest_report_date(value: Any) -> str:
    if not value:
        return "--"
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, _dt.datetime):
        return value.astimezone().date().isoformat()
    s = str(value).strip()
    if not s:
        return "--"
    return s[:10]

def _backtest_report_inr(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "--"
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}"

def _backtest_report_pct(value: Any) -> str:
    if value is None:
        return "--"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "--"
    return f"{f * 100.0:.2f}%"

def _backtest_report_ratio(value: Any) -> str:
    if value is None:
        return "--"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "--"
    if f == int(f):
        return f"{int(f):.0f}"
    return f"{f:.4f}"


def _backtest_report_markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a compact Markdown table, or an explicit empty-state note."""
    if not rows:
        return "_No data available._"
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\\n".join(lines)


def build_backtest_markdown_report(
    results_by_horizon: dict[int, Any], metadata: dict
) -> str:
    """Build a concise, LLM-ready Markdown report from backtest results."""
    results = results_by_horizon or {}
    meta = metadata or {}

    generated_at = str(
        meta.get("generated_at") or _dt.datetime.now().astimezone().isoformat()
    )
    profile = str(meta.get("profile") or meta.get("person_id") or "All profiles")
    compare_watchlist = _backtest_report_bool(meta.get("compare_watchlist"))
    include_closed = _backtest_report_bool(meta.get("include_closed"), True)
    price_cache_date = _backtest_report_date(meta.get("price_cache_date"))

    lines: list[str] = [
        "# Stage 4 Backtest AI Report",
        "",
        "## Backtest Overview",
        "",
        f"- Generated: {generated_at}",
        f"- Profile: {profile}",
        f"- Compare With Planned Watchlist Additions: {'Yes' if compare_watchlist else 'No'}",
        f"- Include Closed Positions: {'Yes' if include_closed else 'No'}",
        f"- Price-Cache Date: {price_cache_date}",
        "",
    ]

    horizons: list[int] = []
    for horizon in results:
        try:
            horizons.append(int(horizon))
        except (TypeError, ValueError):
            continue
    horizons = sorted(set(horizons))

    if not horizons:
        lines.extend(
            [
                "## Results",
                "",
                "_No backtest results were available for the requested horizons._",
                "",
            ]
        )
        return "\\n".join(lines)

    longest_horizon = horizons[-1]
    longest_result = results.get(longest_horizon) or results.get(str(longest_horizon)) or {}
    longest_config = longest_result.get("config") or {}
    strategy_years = longest_config.get("years") or longest_horizon

    lines.extend(
        [
            "## Strategy Parameters",
            "",
            f"- Backtest Horizon: {strategy_years} years",
            f"- Compare With Planned Watchlist Additions: {'Yes' if compare_watchlist else 'No'}",
            f"- Include Closed Positions: {'Yes' if include_closed else 'No'}",
            f"- Base Unit SIP: {_backtest_report_inr(longest_config.get('base_unit_sip'))}",
            f"- Max Monthly Ceiling: {_backtest_report_inr(longest_config.get('max_monthly_ceiling'))}",
            "",
            "## Cross-Horizon Comparison Scorecard",
            "",
        ]
    )

    def _horizon_to_str(hor: int) -> str:
        try:
            return str(int(hor))
        except (TypeError, ValueError):
            return str(hor)

    def _make_scorecard_row(horizon_result: dict) -> list[str]:
        if not isinstance(horizon_result, dict):
            return [
                _horizon_to_str(horizon_result),
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                "N/A",
            ]
        sc = horizon_result.get("scorecard", {}) if isinstance(horizon_result, dict) else {}
        bench = horizon_result.get("benchmarks", {}) if isinstance(horizon_result, dict) else {}
        n50 = bench.get("nifty50", {})
        n500 = bench.get("nifty500", {})
        ew = bench.get("equal_weight", {})
        return [
            _horizon_to_str(horizon_result),
            _backtest_report_inr(sc.get("invested")),
            _backtest_report_inr(sc.get("realized_sells")),
            _backtest_report_inr(sc.get("final_net_value")),
            _backtest_report_pct(sc.get("post_tax_xirr_pct")),
            _backtest_report_pct(ew.get("post_tax_xirr_pct")),
            _backtest_report_pct(n50.get("post_tax_xirr_pct")),
            _backtest_report_pct(n500.get("post_tax_xirr_pct")),
            _backtest_report_pct(sc.get("max_drawdown_pct")),
            _backtest_report_ratio(sc.get("return_drawdown_ratio")),
            _backtest_report_inr(sc.get("friction_paid")),
            str(sc.get("trade_count", 0)),
        ]

    scorecard_rows: list[list[str]] = []
    for horizon in horizons:
        res = results.get(horizon) or results.get(str(horizon)) or {}
        scorecard_rows.append(_make_scorecard_row(res))

    lines.append(
        _backtest_report_markdown_table(
            [
                "Horizon",
                "Invested (₹)",
                "Realized Sells (₹)",
                "Final Net Value (₹)",
                "Strategy XIRR (%)",
                "Equal-Weight XIRR (%)",
                "Nifty 50 XIRR (%)",
                "Nifty 500 XIRR (%)",
                "Max DD (%)",
                "Return/DD",
                "Friction (₹)",
                "Trades",
            ],
            scorecard_rows,
        )
    )
    lines.extend(
        [
            "",
            "_Raw daily time-series arrays are omitted to keep this report compact and token-efficient._",
            "",
        ]
    )

    return "\\n".join(lines)
