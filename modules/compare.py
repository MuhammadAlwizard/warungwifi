"""Comparison logic for two uploaded files; never mutates master data."""

from __future__ import annotations

import pandas as pd

from .data_io import MASTER_COLUMNS, validate_entries


def compare_frames(file_a: pd.DataFrame, file_b: pd.DataFrame) -> pd.DataFrame:
    a = validate_entries(file_a, source="File A")[['tanggal', 'pendapatan']].rename(columns={'pendapatan': 'pendapatan_A'})
    b = validate_entries(file_b, source="File B")[['tanggal', 'pendapatan']].rename(columns={'pendapatan': 'pendapatan_B'})
    result = a.merge(b, on='tanggal', how='outer').sort_values('tanggal').reset_index(drop=True)
    result['pendapatan_A'] = result['pendapatan_A'].fillna(0.0)
    result['pendapatan_B'] = result['pendapatan_B'].fillna(0.0)
    result['selisih'] = result['pendapatan_B'] - result['pendapatan_A']
    result['% perubahan'] = result.apply(
        lambda row: None if row['pendapatan_A'] == 0 and row['pendapatan_B'] == 0
        else (float('inf') if row['pendapatan_A'] == 0 else row['selisih'] / row['pendapatan_A'] * 100), axis=1
    )
    return result


def comparison_summary(comparison: pd.DataFrame) -> dict[str, object | float | None]:
    if comparison.empty:
        return {'total_A': 0.0, 'total_B': 0.0, 'total_selisih': 0.0, 'terbesar_naik': None, 'terbesar_turun': None}
    increases = comparison.loc[comparison['selisih'].idxmax()]
    decreases = comparison.loc[comparison['selisih'].idxmin()]
    return {
        'total_A': float(comparison['pendapatan_A'].sum()),
        'total_B': float(comparison['pendapatan_B'].sum()),
        'total_selisih': float(comparison['selisih'].sum()),
        'terbesar_naik': increases,
        'terbesar_turun': decreases,
    }
