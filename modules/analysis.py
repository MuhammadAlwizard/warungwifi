"""Pandas-based statistics and trend calculations."""

from __future__ import annotations

import calendar

import pandas as pd


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    result = df.copy()
    result["tanggal"] = pd.to_datetime(result["tanggal"])
    result["pendapatan"] = pd.to_numeric(result["pendapatan"])
    return result.sort_values("tanggal").reset_index(drop=True)


def filter_dates(df: pd.DataFrame, start: object, end: object) -> pd.DataFrame:
    frame = _clean(df)
    if frame.empty:
        return frame
    return frame[(frame["tanggal"].dt.date >= start) & (frame["tanggal"].dt.date <= end)].reset_index(drop=True)


def descriptive_stats(df: pd.DataFrame) -> dict[str, float | int]:
    frame = _clean(df)
    if frame.empty:
        return {"count": 0, "total": 0.0, "mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0}
    values = frame["pendapatan"]
    return {
        "count": int(values.count()),
        "total": float(values.sum()),
        "mean": float(values.mean()),
        "median": float(values.median()),
        "min": float(values.min()),
        "max": float(values.max()),
    }


def monthly_aggregate(df: pd.DataFrame, year: int) -> pd.DataFrame:
    frame = _clean(df)
    months = pd.DataFrame({"bulan": range(1, 13)})
    if not frame.empty:
        grouped = frame[frame["tanggal"].dt.year == year].groupby(frame["tanggal"].dt.month)["pendapatan"].sum()
        months["pendapatan"] = months["bulan"].map(grouped).fillna(0.0)
    else:
        months["pendapatan"] = 0.0
    months["tahun"] = year
    return months[["tahun", "bulan", "pendapatan"]]


def daily_aggregate(df: pd.DataFrame, month: int, year: int) -> pd.DataFrame:
    frame = _clean(df)
    days = calendar.monthrange(year, month)[1]
    calendar_frame = pd.DataFrame({"tanggal": pd.date_range(f"{year}-{month:02d}-01", periods=days, freq="D")})
    if not frame.empty:
        grouped = frame[(frame["tanggal"].dt.year == year) & (frame["tanggal"].dt.month == month)].groupby("tanggal")["pendapatan"].sum()
        calendar_frame["pendapatan"] = calendar_frame["tanggal"].map(grouped).fillna(0.0)
    else:
        calendar_frame["pendapatan"] = 0.0
    return calendar_frame


def trend_data(df: pd.DataFrame, moving_window: int = 7) -> pd.DataFrame:
    frame = _clean(df)
    if frame.empty:
        return pd.DataFrame(columns=["tanggal", "pendapatan", "perubahan_pct", "moving_average"])
    result = frame.groupby("tanggal", as_index=False)["pendapatan"].sum().sort_values("tanggal")
    result["perubahan_pct"] = result["pendapatan"].pct_change() * 100
    result["moving_average"] = result["pendapatan"].rolling(moving_window, min_periods=1).mean()
    return result


def monthly_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate income by month and calculate month-over-month change."""

    frame = _clean(df)
    if frame.empty:
        return pd.DataFrame(columns=["bulan", "pendapatan", "perubahan_pct"])
    result = frame.assign(bulan=frame["tanggal"].dt.to_period("M")).groupby("bulan", as_index=False)["pendapatan"].sum()
    result["perubahan_pct"] = result["pendapatan"].pct_change() * 100
    return result


def extremes(df: pd.DataFrame, period: str = "day") -> dict[str, object | None]:
    frame = _clean(df)
    if frame.empty:
        return {"highest": None, "lowest": None}
    if period == "month":
        grouped = frame.groupby(frame["tanggal"].dt.to_period("M"))["pendapatan"].sum()
    else:
        grouped = frame.groupby("tanggal")["pendapatan"].sum()
    return {"highest": grouped.idxmax(), "highest_value": float(grouped.max()), "lowest": grouped.idxmin(), "lowest_value": float(grouped.min())}


def period_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None if current == 0 else float("inf")
    return (current - previous) / previous * 100


def yearly_monthly_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot income into one row per (tahun, bulan) across every year present."""

    frame = _clean(df)
    if frame.empty:
        return pd.DataFrame(columns=["tahun", "bulan", "pendapatan"])
    grouped = frame.groupby([frame["tanggal"].dt.year, frame["tanggal"].dt.month])["pendapatan"].sum()
    grouped.index.names = ["tahun", "bulan"]
    return grouped.reset_index()


def top_bottom_days(df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """Return the n highest and n lowest days (by date, summed), tagged by kategori."""

    frame = _clean(df)
    if frame.empty:
        return pd.DataFrame(columns=["tanggal", "pendapatan", "kategori"])
    ranked = frame.groupby("tanggal", as_index=False)["pendapatan"].sum()
    n = min(n, len(ranked))
    top = ranked.nlargest(n, "pendapatan").assign(kategori="Tertinggi")
    bottom = ranked.nsmallest(n, "pendapatan").assign(kategori="Terendah")
    return pd.concat([top, bottom], ignore_index=True).sort_values("pendapatan", ascending=False).reset_index(drop=True)
