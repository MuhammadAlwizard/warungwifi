"""Plotly chart builders used by the Streamlit UI."""

from __future__ import annotations

import calendar

import pandas as pd
import plotly.graph_objects as go


MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

# Jewel-tone palette: each hue is genuinely different (not tints of one color), so series
# inside a single chart stay easy to tell apart, while the set as a whole still reads as rich
# and feminine rather than muted grey-pink.
MAGENTA = "#a3195b"
AMETHYST = "#6b2fa0"
TEAL = "#0f6d6a"
GOLD = "#a67c00"
BURGUNDY = "#800020"
PIE_COLORS = ["#a3195b", "#0f6d6a", "#6b2fa0", "#a67c00", "#800020", "#3b7d8c", "#d6336c", "#4a1942", "#c2410c", "#8f5fd1", "#2f8f6b", "#c9a227"]
LINE_COLORWAY = [MAGENTA, TEAL, AMETHYST, GOLD, BURGUNDY]

FONT = dict(family="Mulish, sans-serif", color="#4a1942")


def _rupiah(value: float) -> str:
    return f"Rp {value:,.0f}".replace(",", ".")


def _base_layout(fig: go.Figure, **kwargs) -> go.Figure:
    fig.update_layout(
        font=FONT,
        title_font=dict(family="Sora, sans-serif", size=18, color="#4a1942"),
        plot_bgcolor="#fff8fa",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=55, b=10),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        **kwargs,
    )
    fig.update_xaxes(gridcolor="#f0dde6")
    fig.update_yaxes(gridcolor="#f0dde6")
    return fig


def monthly_chart(monthly: pd.DataFrame, year: int) -> go.Figure:
    fig = go.Figure(go.Bar(x=MONTHS, y=monthly["pendapatan"], marker_color=MAGENTA, marker_line_width=0, hovertemplate="%{x}: %{customdata}<extra></extra>", customdata=[_rupiah(x) for x in monthly["pendapatan"]]))
    _base_layout(fig, title=f"Pendapatan Bulanan {year}", xaxis_title="Bulan", yaxis_title="Pendapatan (Rp)", height=360)
    fig.update_yaxes(tickprefix="Rp ", separatethousands=True)
    return fig


def daily_chart(daily: pd.DataFrame, month: int, year: int) -> go.Figure:
    labels = daily["tanggal"].dt.day if not daily.empty else []
    fig = go.Figure(go.Bar(x=labels, y=daily["pendapatan"], marker_color=AMETHYST, marker_line_width=0, hovertemplate="Tanggal %{x}: %{customdata}<extra></extra>", customdata=[_rupiah(x) for x in daily["pendapatan"]]))
    _base_layout(fig, title=f"Pendapatan Harian {calendar.month_name[month]} {year}", xaxis_title="Tanggal", yaxis_title="Pendapatan (Rp)", height=360)
    # Force full digits with dot thousand-separators on the y-axis instead of Plotly's default "10M"/"100K" abbreviation.
    fig.update_layout(separators=",.")
    fig.update_yaxes(tickprefix="Rp ", tickformat=",.0f")
    return fig


def trend_chart(trend: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not trend.empty:
        fig.add_trace(go.Scatter(x=trend["tanggal"], y=trend["pendapatan"], mode="lines+markers", name="Pendapatan", line=dict(color=MAGENTA, width=3), marker=dict(size=6)))
        fig.add_trace(go.Scatter(x=trend["tanggal"], y=trend["moving_average"], mode="lines", name="Rata-rata bergerak", line=dict(color=TEAL, dash="dash", width=2)))
    _base_layout(fig, title="Tren Pendapatan Harian", xaxis_title="Tanggal", yaxis_title="Pendapatan (Rp)", hovermode="x unified", height=420)
    fig.update_yaxes(tickprefix="Rp ", separatethousands=True)
    return fig


def comparison_chart(comparison: pd.DataFrame) -> go.Figure:
    colors = [MAGENTA if value >= 0 else TEAL for value in comparison["selisih"]]
    fig = go.Figure(go.Bar(x=comparison["tanggal"], y=comparison["selisih"], marker_color=colors, marker_line_width=0, name="Selisih", hovertemplate="%{x|%d %b %Y}: %{y:,.0f}<extra></extra>"))
    _base_layout(fig, title="Selisih File B - File A", xaxis_title="Tanggal", yaxis_title="Selisih (Rp)", height=420)
    fig.update_yaxes(tickprefix="Rp ", separatethousands=True)
    return fig


def monthly_pie(monthly: pd.DataFrame, year: int) -> go.Figure:
    fig = go.Figure(go.Pie(labels=MONTHS, values=monthly["pendapatan"], hole=0.45, marker=dict(colors=PIE_COLORS, line=dict(color="#fff8fa", width=2))))
    _base_layout(fig, title=f"Kontribusi Bulan {year}")
    return fig


def yearly_monthly_chart(matrix: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for year, group in matrix.groupby("tahun"):
        ordered = group.set_index("bulan").reindex(range(1, 13)).fillna(0.0).reset_index()
        fig.add_trace(go.Scatter(
            x=MONTHS, y=ordered["pendapatan"], mode="lines+markers", name=str(int(year)),
            hovertemplate="%{x}: %{customdata}<extra></extra>", customdata=[_rupiah(v) for v in ordered["pendapatan"]],
        ))
    _base_layout(fig, title="Tren Bulanan Antar Tahun", xaxis_title="Bulan", yaxis_title="Pendapatan (Rp)", hovermode="x unified", height=380, colorway=LINE_COLORWAY)
    fig.update_yaxes(tickprefix="Rp ", separatethousands=True)
    return fig


def top_bottom_chart(ranked: pd.DataFrame) -> go.Figure:
    colors = [MAGENTA if k == "Tertinggi" else AMETHYST for k in ranked["kategori"]]
    labels = ranked["tanggal"].dt.strftime("%d %b %Y")
    fig = go.Figure(go.Bar(
        x=labels, y=ranked["pendapatan"], marker_color=colors, marker_line_width=0,
        hovertemplate="%{x}: %{customdata}<extra></extra>", customdata=[_rupiah(v) for v in ranked["pendapatan"]],
    ))
    _base_layout(fig, title="Hari Tertinggi & Terendah", xaxis_title="Tanggal", yaxis_title="Pendapatan (Rp)", height=380)
    fig.update_yaxes(tickprefix="Rp ", separatethousands=True)
    return fig


def two_month_line_chart(days_a: pd.DataFrame, days_b: pd.DataFrame, label_a: str, label_b: str) -> go.Figure:
    """Line chart comparing two months day-by-day, plus a Selisih (B - A) line.

    All three series share one unified hover box per day-of-month, so the user can
    read off the exact difference for any date (e.g. tanggal 1 bulan A vs tanggal 1 bulan B).
    """

    n = min(len(days_a), len(days_b))
    x = list(range(1, n + 1))
    a_vals = days_a["pendapatan"].iloc[:n].reset_index(drop=True)
    b_vals = days_b["pendapatan"].iloc[:n].reset_index(drop=True)
    selisih = b_vals - a_vals

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=a_vals, mode="lines+markers", name=label_a, line=dict(color=MAGENTA, width=2.5), marker=dict(size=6), hovertemplate="Rp %{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=b_vals, mode="lines+markers", name=label_b, line=dict(color=GOLD, width=2.5), marker=dict(size=6), hovertemplate="Rp %{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=selisih, mode="lines+markers", name="Selisih", line=dict(color=TEAL, width=2.5), marker=dict(size=6), hovertemplate="Rp %{y:,.0f}<extra></extra>"))
    _base_layout(fig, title=f"Perbandingan {label_a} vs {label_b}", xaxis_title="Tanggal ke-", yaxis_title="Pendapatan (Rp)", hovermode="x unified", height=380)
    fig.update_layout(separators=",.")
    fig.update_yaxes(tickprefix="Rp ", tickformat=",.0f")
    return fig
