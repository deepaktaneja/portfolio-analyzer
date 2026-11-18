"""Custom Plotly charts for the dashboard."""
from __future__ import annotations

import plotly.graph_objects as go

try:
    from plotly.colors import qualitative as _qual
    _PALETTE = list(getattr(_qual, "Alphabet", None) or getattr(_qual, "Plotly", None) or [])
except Exception:  # noqa: BLE001
    _PALETTE = []
if not _PALETTE:
    _PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]


def _color(i: int) -> str:
    return _PALETTE[i % len(_PALETTE)]


def render_concentration_chart(hdf, max_bars: int = 8):
    """Concentration bar chart: descending, % on bars, remainder grouped as Others.
 
    The Others bar is rendered as stacked colour segments (one per company) so
    each company has its own colour and hover tooltip: "Company: X.XX%".
    """
    if hdf is None or getattr(hdf, "empty", True) or "weight_%" not in hdf.columns:
        return None
    df = hdf[hdf["weight_%"].notna()].copy()
    if df.empty:
        return None
    df = df.sort_values("weight_%", ascending=False)
    label_col = "security_name" if "security_name" in df.columns else "isin"
    df["_label"] = df[label_col].fillna(df.get("isin", "")).astype(str)
    top = df.head(max_bars)
    others = df.iloc[max_bars:]

    fig = go.Figure()
    top_x = list(top["_label"])
    top_y = [float(v) for v in top["weight_%"]]
    top_hover = []
    for _, row in top.iterrows():
        val = row.get("current_value")
        try:
            val_s = "Rs " + format(float(val), ",.0f")
        except (TypeError, ValueError):
            val_s = "N/A"
        _name = row["_label"]
        _w = float(row["weight_%"])
        top_hover.append(f"{_name}<br>Weight: {_w:.2f}%<br>Value: {val_s}")
    fig.add_trace(go.Bar(
        x=top_x,
        y=top_y,
        text=[f"{v:.2f}%" for v in top_y],
        textposition="outside",
        cliponaxis=False,
        marker_color=[_color(i) for i in range(len(top_x))],
        hovertext=top_hover,
        hovertemplate="%{hovertext}<extra></extra>",
        name="Holdings",
    ))

    others_total = 0.0
    if not others.empty:
        others_total = float(others["weight_%"].sum())
        for j, (_, row) in enumerate(others.iterrows()):
            pct = float(row["weight_%"])
            label = row["_label"]
            fig.add_trace(go.Bar(
                x=["Others"],
                y=[pct],
                name=label,
                marker_color=_color(len(top_x) + j),
                hovertext=[label],
                hovertemplate="%{hovertext}: %{y:.2f}%<extra></extra>",
                showlegend=False,
            ))
        fig.add_annotation(x="Others", y=others_total, text=f"{others_total:.2f}%", showarrow=False, yshift=10)

    peak = max(top_y + [others_total]) if top_y else 100.0
    fig.update_layout(
        barmode="stack",
        yaxis_title="Weight (%)",
        yaxis={"range": [0, peak * 1.18]},
        showlegend=False,
        height=430,
        margin={"t": 40, "b": 120, "l": 55, "r": 30},
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(tickangle=-25)
    return fig


def render_sector_chart(data):
    """Sector allocation bar chart: descending order, % labels, hover with value."""
    import pandas as pd

    if data is None:
        return None
    if isinstance(data, pd.DataFrame):
        if "sector" not in data.columns or "current_value" not in data.columns:
            return None
        series = data.groupby("sector", dropna=True)["current_value"].sum()
    else:
        series = data
    if series is None or len(series) == 0:
        return None
    series = series.dropna()
    series = series[series > 0]
    if series.empty:
        return None
    total = float(series.sum())
    if total <= 0:
        return None
    series = series.sort_values(ascending=False)
    labels = [str(x) for x in series.index]
    vals = [float(v) for v in series.values]
    pct = [v / total * 100.0 for v in vals]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels,
        y=pct,
        text=[f"{p:.2f}%" for p in pct],
        textposition="outside",
        cliponaxis=False,
        marker_color=[_color(i) for i in range(len(labels))],
        customdata=vals,
        hovertemplate=(
            "%{x}<br>Allocation: %{y:.2f}%<br>"
            "Value: Rs %{customdata:,.0f}<extra></extra>"
        ),
        name="Sector",
    ))
    peak = max(pct) if pct else 100.0
    fig.update_layout(
        yaxis_title="Allocation (% of equity)",
        yaxis={"range": [0, peak * 1.18]},
        showlegend=False,
        height=420,
        margin={"t": 40, "b": 120, "l": 55, "r": 30},
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(categoryorder="array", categoryarray=labels, tickangle=-25)
    return fig
