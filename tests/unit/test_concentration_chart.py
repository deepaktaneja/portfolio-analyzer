"""Tests for the enhanced concentration chart."""
from __future__ import annotations

import pandas as pd

from app.components.charts import render_concentration_chart


def _frame(n):
    rows = []
    for i in range(n):
        rows.append({"security_name": "CO" + str(i), "isin": "INE" + str(i), "weight_%": float(n - i), "current_value": float((n - i) * 1000)})
    return pd.DataFrame(rows)


def test_none_when_empty():
    assert render_concentration_chart(None) is None
    assert render_concentration_chart(pd.DataFrame()) is None


def test_descending_and_labels():
    fig = render_concentration_chart(_frame(5), max_bars=8)
    assert fig is not None
    assert len(fig.data) == 1
    assert list(fig.data[0].x) == ["CO0", "CO1", "CO2", "CO3", "CO4"]
    assert list(fig.data[0].text) == ["5.00%", "4.00%", "3.00%", "2.00%", "1.00%"]
    assert fig.data[0].textposition == "outside"


def test_others_stacked_with_hover():
    fig = render_concentration_chart(_frame(12), max_bars=8)
    assert fig is not None
    assert len(fig.data) == 5
    others = fig.data[1:]
    assert all(list(t.x) == ["Others"] for t in others)
    assert [t.name for t in others] == ["CO8", "CO9", "CO10", "CO11"]
    assert all(":" in str(t.hovertemplate) for t in others)
    assert round(sum(float(t.y[0]) for t in others), 2) == 10.0
    assert fig.layout.annotations[0].text == "10.00%"


def test_others_absent_when_fewer_than_max():
    fig = render_concentration_chart(_frame(3), max_bars=8)
    assert fig is not None
    assert len(fig.data) == 1
    assert not fig.layout.annotations


def test_sector_chart_descending_and_percent():
    import pandas as pd

    from app.components.charts import render_sector_chart
    s = pd.Series({"Tech": 500.0, "Energy": 300.0, "Banks": 200.0})
    fig = render_sector_chart(s)
    assert fig is not None
    assert list(fig.data[0].x) == ["Tech", "Energy", "Banks"]
    assert list(fig.data[0].text) == ["50.00%", "30.00%", "20.00%"]
    assert fig.data[0].textposition == "outside"
    assert fig.layout.xaxis.categoryorder == "array"
    assert "Allocation" in str(fig.data[0].hovertemplate)
