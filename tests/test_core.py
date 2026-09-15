import shutil
from pathlib import Path

import pandas as pd

import modules.data_io as data_io
from modules.analysis import daily_aggregate, monthly_aggregate
from modules.compare import compare_frames, comparison_summary


def test_core_workflow_without_touching_project_data():
    temp_dir = data_io.PROJECT_ROOT / ".test-runtime"
    shutil.rmtree(temp_dir, ignore_errors=True)
    data_io.DATA_DIR = temp_dir / "data"
    data_io.UPLOAD_DIR = data_io.DATA_DIR / "uploads"
    data_io.DB_PATH = data_io.DATA_DIR / "warungwifi.db"
    data_io.LEGACY_MASTER_PATH = data_io.DATA_DIR / "master.csv"
    data_io.LEGACY_CHANGE_LOG_PATH = data_io.DATA_DIR / "change_log.csv"

    master = data_io.load_master()
    first = data_io.validate_entries(
        pd.DataFrame([{"tanggal": "2026-01-01", "pendapatan": 100, "catatan": "a"}]),
        source="test.csv",
    )
    master, changed = data_io.apply_import(master, first)
    assert changed == 1
    assert len(master) == 1

    conflict = data_io.validate_entries(
        pd.DataFrame([{"tanggal": "2026-01-01", "pendapatan": 50}]), source="test2.csv"
    )
    master, changed = data_io.apply_import(master, conflict, {"2026-01-01": "jumlahkan"})
    assert changed == 1
    assert float(master.iloc[0].pendapatan) == 150
    assert len(monthly_aggregate(master, 2026)) == 12
    assert len(daily_aggregate(master, 1, 2026)) == 31

    comparison = compare_frames(first, conflict)
    assert float(comparison.iloc[0].selisih) == -50
    assert comparison_summary(comparison)["total_selisih"] == -50

    master = data_io.edit_entry(master, "2026-01-01", 175, "edit")
    master = data_io.delete_entry(master, "2026-01-01")
    assert master.empty
    assert len(data_io.load_change_log()) == 4
