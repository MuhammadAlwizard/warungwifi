import os
import shutil
from pathlib import Path

import pandas as pd
import pytest

import modules.data_io as data_io
from modules.analysis import daily_aggregate, monthly_aggregate
from modules.compare import compare_frames, comparison_summary


@pytest.fixture(params=["sqlite", "mariadb"])
def db_mode(request, monkeypatch):
    """Parametrized fixture to run tests in both SQLite and MariaDB modes.
    MariaDB tests are skipped if TEST_DATABASE_URL is not set."""

    if request.param == "mariadb":
        test_db_url = os.environ.get("TEST_DATABASE_URL")
        if not test_db_url:
            pytest.skip("TEST_DATABASE_URL not set; skipping MariaDB tests")
        monkeypatch.setenv("DATABASE_URL", test_db_url)
    else:
        # SQLite mode: use test directory
        monkeypatch.delenv("DATABASE_URL", raising=False)

    return request.param


@pytest.fixture
def isolated_db(db_mode, monkeypatch):
    """Set up isolated database for testing.
    For SQLite: redirects to .test-runtime directory.
    For MariaDB: drops and recreates tables.
    """

    if db_mode == "sqlite":
        # SQLite mode: use isolated directory
        temp_dir = data_io.PROJECT_ROOT / ".test-runtime"
        shutil.rmtree(temp_dir, ignore_errors=True)
        monkeypatch.setattr(data_io, "DATA_DIR", temp_dir / "data")
        monkeypatch.setattr(data_io, "UPLOAD_DIR", data_io.DATA_DIR / "uploads")
        monkeypatch.setattr(data_io, "DB_PATH", data_io.DATA_DIR / "warungwifi.db")
        monkeypatch.setattr(data_io, "LEGACY_MASTER_PATH", data_io.DATA_DIR / "master.csv")
        monkeypatch.setattr(data_io, "LEGACY_CHANGE_LOG_PATH", data_io.DATA_DIR / "change_log.csv")
    else:
        # MariaDB mode: drop and recreate tables
        with data_io._connect() as conn:
            if data_io._is_mariadb_mode():
                cursor = conn.cursor()
                cursor.execute("DROP TABLE IF EXISTS uploads")
                cursor.execute("DROP TABLE IF EXISTS change_log")
                cursor.execute("DROP TABLE IF EXISTS entries")
                cursor.close()

    # Initialize database
    data_io.ensure_data_files()

    yield

    # Cleanup for SQLite (MariaDB cleanup is optional)
    if db_mode == "sqlite":
        temp_dir = data_io.PROJECT_ROOT / ".test-runtime"
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_core_workflow_without_touching_project_data(isolated_db, db_mode):
    """Original test: core workflow with imports and edits."""

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


def test_lost_update_fix(isolated_db, db_mode):
    """Test that concurrent users with stale copies don't overwrite each other.

    Simulates:
    1. User A loads master (stale copy)
    2. User B loads master (stale copy)
    3. User A applies import for date X -> master now has X
    4. User B applies import for date Y -> master should have both X and Y

    This proves the lost-update bug is fixed.
    """

    # Initial state: empty
    master_a = data_io.load_master()
    master_b = data_io.load_master()

    # User A imports date 2026-01-01 with amount 100
    entry_a = data_io.validate_entries(
        pd.DataFrame([{"tanggal": "2026-01-01", "pendapatan": 100, "catatan": "entry A"}]),
        source="user_a.csv",
    )
    master_a, _ = data_io.apply_import(master_a, entry_a)
    assert len(master_a) == 1

    # User B imports date 2026-01-02 with amount 200
    # Using the old (stale) master_b from before A's import
    entry_b = data_io.validate_entries(
        pd.DataFrame([{"tanggal": "2026-01-02", "pendapatan": 200, "catatan": "entry B"}]),
        source="user_b.csv",
    )
    master_b, _ = data_io.apply_import(master_b, entry_b)

    # Reload from DB: should have both entries
    final_master = data_io.load_master()
    assert len(final_master) == 2
    assert final_master["tanggal"].dt.strftime("%Y-%m-%d").tolist() == ["2026-01-01", "2026-01-02"]


def test_archive_upload_and_count(isolated_db, db_mode):
    """Test that archive_upload and count_uploads work correctly."""

    # Start with no uploads
    assert data_io.count_uploads() == 0

    # Archive first upload
    data_io.archive_upload("test_file_1.csv", b"test content 1")
    assert data_io.count_uploads() == 1

    # Archive second upload
    data_io.archive_upload("test_file_2.xlsx", b"test content 2")
    assert data_io.count_uploads() == 2

    # Archive with special characters in filename (should be sanitized)
    data_io.archive_upload("test@file#3!.csv", b"test content 3")
    assert data_io.count_uploads() == 3


def test_edit_entry_requires_existing_date(isolated_db, db_mode):
    """Test that edit_entry raises error if date doesn't exist."""

    master = data_io.load_master()

    # Try to edit a non-existent date
    with pytest.raises(data_io.DataValidationError, match="Tanggal yang akan diedit tidak ditemukan"):
        data_io.edit_entry(master, "2026-01-01", 100)


def test_delete_entry_requires_existing_date(isolated_db, db_mode):
    """Test that delete_entry raises error if date doesn't exist."""

    master = data_io.load_master()

    # Try to delete a non-existent date
    with pytest.raises(data_io.DataValidationError, match="Tanggal yang akan dihapus tidak ditemukan"):
        data_io.delete_entry(master, "2026-01-01")


def test_add_manual_entry_new_date(isolated_db, db_mode):
    """Test adding a manual entry to an empty master."""

    master = data_io.load_master()
    assert master.empty

    entry = {"tanggal": "2026-01-01", "pendapatan": 100, "catatan": "manual entry"}
    new_master, changed = data_io.add_manual_entry(master, entry)

    assert changed is True
    assert len(new_master) == 1
    assert float(new_master.iloc[0]["pendapatan"]) == 100


def test_apply_import_with_lewati_action(isolated_db, db_mode):
    """Test that lewati action skips conflicting dates."""

    master = data_io.load_master()

    # Add initial entry
    entry1 = data_io.validate_entries(
        pd.DataFrame([{"tanggal": "2026-01-01", "pendapatan": 100}]),
        source="test.csv",
    )
    master, _ = data_io.apply_import(master, entry1)
    assert len(master) == 1
    assert float(master.iloc[0]["pendapatan"]) == 100

    # Try to import different amount with lewati action
    entry2 = data_io.validate_entries(
        pd.DataFrame([{"tanggal": "2026-01-01", "pendapatan": 200}]),
        source="test.csv",
    )
    master, changed = data_io.apply_import(master, entry2, {"2026-01-01": "lewati"})

    # Amount should not change
    assert changed == 0
    assert len(master) == 1
    assert float(master.iloc[0]["pendapatan"]) == 100


def test_apply_import_with_timpa_action(isolated_db, db_mode):
    """Test that timpa action replaces conflicting dates."""

    master = data_io.load_master()

    # Add initial entry
    entry1 = data_io.validate_entries(
        pd.DataFrame([{"tanggal": "2026-01-01", "pendapatan": 100, "catatan": "old"}]),
        source="test.csv",
    )
    master, _ = data_io.apply_import(master, entry1)

    # Import with timpa action
    entry2 = data_io.validate_entries(
        pd.DataFrame([{"tanggal": "2026-01-01", "pendapatan": 200, "catatan": "new"}]),
        source="test.csv",
    )
    master, changed = data_io.apply_import(master, entry2, {"2026-01-01": "timpa"})

    assert changed == 1
    assert len(master) == 1
    assert float(master.iloc[0]["pendapatan"]) == 200
    assert master.iloc[0]["catatan"] == "new"


def test_apply_import_identical_amount_is_lewati(isolated_db, db_mode):
    """Test that identical amount always logs lewati, even if action says otherwise."""

    master = data_io.load_master()

    # Add initial entry
    entry1 = data_io.validate_entries(
        pd.DataFrame([{"tanggal": "2026-01-01", "pendapatan": 100}]),
        source="test.csv",
    )
    master, _ = data_io.apply_import(master, entry1)

    # Import same amount with timpa action
    entry2 = data_io.validate_entries(
        pd.DataFrame([{"tanggal": "2026-01-01", "pendapatan": 100}]),
        source="test.csv",
    )
    master, changed = data_io.apply_import(master, entry2, {"2026-01-01": "timpa"})

    # Should be treated as lewati (no change)
    assert changed == 0
    log = data_io.load_change_log()
    # Find the last log entry for this date
    last_entries = log[log["tanggal data"] == "2026-01-01"]
    assert last_entries.iloc[-1]["aksi"] == "lewati"
