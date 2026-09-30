"""Data storage, validation, imports, and audit logging.

The application stores normalized entries plus the change log in either:
- A local SQLite database (data/warungwifi.db) for local development,
- A MariaDB database when DATABASE_URL environment variable is set.

Upload archives are stored in the database (uploads table).
Every write goes through one transaction, so a crash or power loss mid-write
leaves the previous data intact instead of a half-written state.
"""

from __future__ import annotations

import io
import json
import functools
import os
import random
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "warungwifi.db"
LEGACY_MASTER_PATH = DATA_DIR / "master.csv"
LEGACY_CHANGE_LOG_PATH = DATA_DIR / "change_log.csv"

MASTER_COLUMNS = ["tanggal", "pendapatan", "catatan", "sumber"]
LOG_COLUMNS = ["waktu perubahan", "tanggal data", "nilai lama", "nilai baru", "sumber", "aksi"]

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "tanggal": ("tanggal", "tgl", "date", "tanggal transaksi"),
    "pendapatan": (
        "pendapatan", "revenue", "income", "nominal", "jumlah", "total",
        "amount", "omzet", "omset", "pemasukan", "penjualan", "total pendapatan",
    ),
    "catatan": ("catatan", "note", "notes", "keterangan", "deskripsi"),
}

_MONTH_NAME_TO_NUM = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
}
_MONTH_ABBR_TO_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "mei": 5, "jun": 6,
    "jul": 7, "aug": 8, "agu": 8, "sep": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "des": 12,
}


class DataValidationError(ValueError):
    """Raised when input data does not satisfy the application schema."""


def _get_database_url() -> str | None:
    """Get DATABASE_URL from environment, or None if not set."""
    return os.environ.get("DATABASE_URL")


def _is_mariadb_mode() -> bool:
    """Check if MariaDB mode is enabled."""
    return _get_database_url() is not None


def _parse_database_url(url: str) -> dict[str, str]:
    """Parse DATABASE_URL in format mysql://USER:PASS@HOST:PORT/DBNAME."""
    parsed = urlparse(url)
    if parsed.scheme != "mysql":
        raise ValueError(f"Invalid DATABASE_URL scheme: {parsed.scheme}. Expected 'mysql'.")
    return {
        "user": parsed.username or "",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "database": parsed.path.lstrip("/") or "",
    }


@contextmanager
def _connect_sqlite(write: bool = False):
    """One transaction per call: commits on success, rolls back on any error.
    ``write=True`` takes the write lock up front (BEGIN IMMEDIATE), so a
    read-decide-write sequence cannot interleave with another writer."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        if write:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _connect_mariadb():
    """One transaction per call for MariaDB: commits on success, rolls back on error."""
    db_url = _get_database_url()
    if not db_url:
        raise RuntimeError("DATABASE_URL not set for MariaDB mode.")

    config = _parse_database_url(db_url)

    try:
        import pymysql
    except ImportError:
        raise ImportError("PyMySQL is required for MariaDB mode. Install with: pip install PyMySQL>=1.1")

    conn = pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute("START TRANSACTION")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _connect(write: bool = False):
    """Get appropriate connection based on backend mode. On MariaDB, writers lock
    the rows they read with SELECT ... FOR UPDATE instead."""
    if _is_mariadb_mode():
        with _connect_mariadb() as conn:
            yield conn
    else:
        with _connect_sqlite(write=write) as conn:
            yield conn


_RETRYABLE_MYSQL_ERRORS = {1205, 1213}  # lock wait timeout, deadlock


def _retry_on_deadlock(func):
    """Re-run a whole write transaction when MariaDB aborts it as a deadlock
    victim. Row locks on dates that do not exist yet are gap locks, so two
    devices adding new dates at the same moment can deadlock; InnoDB rolls one
    back and asks the client to retry, which is safe because nothing was kept."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        attempts = 6
        for attempt in range(attempts):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                code = exc.args[0] if exc.args else None
                if not _is_mariadb_mode() or code not in _RETRYABLE_MYSQL_ERRORS or attempt == attempts - 1:
                    raise
                time.sleep(random.uniform(0.02, 0.1) * (attempt + 1))
    return wrapper


def _execute_query(cursor, query: str, params: tuple | None = None) -> Any:
    """Execute a query with proper parameter handling for both backends."""
    if _is_mariadb_mode():
        # MariaDB/PyMySQL uses %s for parameters
        cursor.execute(query, params or ())
    else:
        # SQLite uses ? for parameters
        cursor.execute(query, params or ())
    return cursor


def _fetchall_as_dicts(cursor) -> list[dict[str, Any]]:
    """Fetch all results as a list of dicts."""
    if _is_mariadb_mode():
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    else:
        # SQLite with row_factory
        return [dict(row) for row in cursor.fetchall()]


def ensure_data_files() -> None:
    """Create SQLite tables (or verify MariaDB tables exist), then migrate any pre-SQLite CSVs.
    In MariaDB mode, skip filesystem directory creation."""

    if not _is_mariadb_mode():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with _connect() as conn:
        if _is_mariadb_mode():
            cursor = conn.cursor()
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS entries (
                    tanggal DATE PRIMARY KEY,
                    pendapatan DOUBLE NOT NULL,
                    catatan TEXT NOT NULL,
                    sumber VARCHAR(255) NOT NULL DEFAULT ''
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS change_log (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    waktu_perubahan VARCHAR(40) NOT NULL,
                    tanggal_data VARCHAR(20) NOT NULL DEFAULT '',
                    nilai_lama VARCHAR(64) NOT NULL DEFAULT '',
                    nilai_baru VARCHAR(64) NOT NULL DEFAULT '',
                    sumber VARCHAR(255) NOT NULL DEFAULT '',
                    aksi VARCHAR(32) NOT NULL DEFAULT ''
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS uploads (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    uploaded_at VARCHAR(40) NOT NULL,
                    file_name VARCHAR(255) NOT NULL,
                    content LONGBLOB NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cursor.close()
        else:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS entries (
                    tanggal TEXT PRIMARY KEY,
                    pendapatan REAL NOT NULL,
                    catatan TEXT NOT NULL DEFAULT '',
                    sumber TEXT NOT NULL DEFAULT ''
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS change_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    waktu_perubahan TEXT NOT NULL,
                    tanggal_data TEXT NOT NULL DEFAULT '',
                    nilai_lama TEXT NOT NULL DEFAULT '',
                    nilai_baru TEXT NOT NULL DEFAULT '',
                    sumber TEXT NOT NULL DEFAULT '',
                    aksi TEXT NOT NULL DEFAULT ''
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uploaded_at TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    content BLOB NOT NULL
                )"""
            )

    if not _is_mariadb_mode():
        _migrate_legacy_csv()


def _migrate_legacy_csv() -> None:
    """One-time import of pre-SQLite CSV data. Only runs while the DB is still
    empty, so a stray leftover CSV can never overwrite data already in SQLite.
    The CSVs are kept, just renamed to ``.migrated``, so nothing is deleted."""

    if not LEGACY_MASTER_PATH.exists() and not LEGACY_CHANGE_LOG_PATH.exists():
        return
    with _connect() as conn:
        already_has_data = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] > 0
        if already_has_data:
            return
        if LEGACY_MASTER_PATH.exists():
            legacy = pd.read_csv(LEGACY_MASTER_PATH)
            if not legacy.empty:
                normalized = validate_entries(legacy, source=None)
                output = normalized.copy()
                output["tanggal"] = output["tanggal"].dt.strftime("%Y-%m-%d")
                output.to_sql("entries", conn, if_exists="append", index=False)
            LEGACY_MASTER_PATH.rename(LEGACY_MASTER_PATH.with_suffix(".csv.migrated"))
        if LEGACY_CHANGE_LOG_PATH.exists():
            legacy_log = pd.read_csv(LEGACY_CHANGE_LOG_PATH, dtype=str).fillna("")
            if not legacy_log.empty:
                legacy_log.columns = ["waktu_perubahan", "tanggal_data", "nilai_lama", "nilai_baru", "sumber", "aksi"]
                legacy_log.to_sql("change_log", conn, if_exists="append", index=False)
            LEGACY_CHANGE_LOG_PATH.rename(LEGACY_CHANGE_LOG_PATH.with_suffix(".csv.migrated"))


def _canon(name: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).strip().lower()).strip()


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Rename common header variants (case, spacing, synonyms) to the canonical schema."""

    lookup = {alias: canonical for canonical, aliases in COLUMN_ALIASES.items() for alias in aliases}
    rename_map: dict[str, str] = {}
    seen: set[str] = set()
    for column in frame.columns:
        canonical = lookup.get(_canon(column))
        if canonical and canonical not in seen and canonical not in frame.columns:
            rename_map[column] = canonical
            seen.add(canonical)
    return frame.rename(columns=rename_map) if rename_map else frame


def _normalize_frame(frame: pd.DataFrame, *, source: str | None = None) -> pd.DataFrame:
    """Normalize a validated frame to the canonical in-memory schema."""

    result = frame.copy()
    result["tanggal"] = pd.to_datetime(result["tanggal"], errors="coerce")
    result["pendapatan"] = pd.to_numeric(result["pendapatan"], errors="coerce")
    result["catatan"] = result.get("catatan", pd.Series("", index=result.index)).fillna("").astype(str)
    if source is not None:
        result["sumber"] = source
    else:
        result["sumber"] = result.get("sumber", pd.Series("", index=result.index)).fillna("").astype(str)
    return result[MASTER_COLUMNS].sort_values("tanggal").reset_index(drop=True)


def validate_entries(frame: pd.DataFrame, *, source: str = "manual") -> pd.DataFrame:
    """Validate and normalize entries, rejecting bad types and duplicate dates."""

    if not isinstance(frame, pd.DataFrame):
        raise DataValidationError("Data harus berupa tabel pandas.")
    frame = _normalize_columns(frame)
    required = {"tanggal", "pendapatan"}
    missing = required - set(frame.columns)
    if missing:
        raise DataValidationError(f"Kolom wajib tidak ada: {', '.join(sorted(missing))}.")
    if frame.empty:
        raise DataValidationError("File tidak berisi data.")

    result = _normalize_frame(frame, source=source)
    if result["tanggal"].isna().any():
        raise DataValidationError("Ada tanggal yang tidak valid. Gunakan format tanggal yang jelas, misalnya YYYY-MM-DD.")
    if result["pendapatan"].isna().any():
        raise DataValidationError("Ada pendapatan yang bukan angka.")
    if (result["pendapatan"] < 0).any():
        raise DataValidationError("Pendapatan tidak boleh negatif.")
    if result["tanggal"].duplicated().any():
        duplicates = result.loc[result["tanggal"].duplicated(keep=False), "tanggal"].dt.strftime("%Y-%m-%d").unique()
        raise DataValidationError(f"Tanggal duplikat dalam file: {', '.join(duplicates)}.")
    return result


def load_master() -> pd.DataFrame:
    """Load the normalized master table using datetime and numeric dtypes."""

    ensure_data_files()
    with _connect() as conn:
        if _is_mariadb_mode():
            cursor = conn.cursor()
            cursor.execute("SELECT tanggal, pendapatan, catatan, sumber FROM entries")
            rows = cursor.fetchall()
            cursor.close()
            frame = pd.DataFrame(rows, columns=["tanggal", "pendapatan", "catatan", "sumber"])
        else:
            frame = pd.read_sql_query("SELECT tanggal, pendapatan, catatan, sumber FROM entries", conn)

    if frame.empty:
        return pd.DataFrame(columns=MASTER_COLUMNS).astype({"tanggal": "datetime64[ns]", "pendapatan": "float64"})
    return validate_entries(frame, source=None)


def _value(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def append_change_logs(records: list[dict[str, object]]) -> None:
    if not records:
        return
    ensure_data_files()
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    rows = [
        (
            record.get("waktu perubahan", timestamp),
            _value(record.get("tanggal data")),
            _value(record.get("nilai lama")),
            _value(record.get("nilai baru")),
            _value(record.get("sumber")),
            _value(record.get("aksi")),
        )
        for record in records
    ]
    with _connect() as conn:
        if _is_mariadb_mode():
            cursor = conn.cursor()
            query = (
                "INSERT INTO change_log (waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi) "
                "VALUES (%s, %s, %s, %s, %s, %s)"
            )
            cursor.executemany(query, rows)
            cursor.close()
        else:
            conn.executemany(
                "INSERT INTO change_log (waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )


def load_change_log() -> pd.DataFrame:
    ensure_data_files()
    with _connect() as conn:
        if _is_mariadb_mode():
            cursor = conn.cursor()
            cursor.execute(
                "SELECT waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi FROM change_log ORDER BY id"
            )
            rows = cursor.fetchall()
            cursor.close()
            frame = pd.DataFrame(rows, columns=["waktu_perubahan", "tanggal_data", "nilai_lama", "nilai_baru", "sumber", "aksi"])
        else:
            frame = pd.read_sql_query(
                "SELECT waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi FROM change_log ORDER BY id",
                conn,
            )

    frame.columns = LOG_COLUMNS
    return frame.fillna("")


def archive_upload(file_name: str, raw_bytes: bytes) -> None:
    """Archive upload bytes in the database."""

    ensure_data_files()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file_name).name).strip("._") or "upload"
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")

    with _connect() as conn:
        if _is_mariadb_mode():
            cursor = conn.cursor()
            query = (
                "INSERT INTO uploads (uploaded_at, file_name, content) "
                "VALUES (%s, %s, %s)"
            )
            cursor.execute(query, (timestamp, safe_name, raw_bytes))
            cursor.close()
        else:
            conn.execute(
                "INSERT INTO uploads (uploaded_at, file_name, content) VALUES (?, ?, ?)",
                (timestamp, safe_name, raw_bytes),
            )


def count_uploads() -> int:
    """Return the count of archived uploads."""

    ensure_data_files()
    with _connect() as conn:
        if _is_mariadb_mode():
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM uploads")
            count = cursor.fetchone()[0]
            cursor.close()
            return count
        else:
            return conn.execute("SELECT COUNT(*) FROM uploads").fetchone()[0]


def _find_day_header_row(raw: pd.DataFrame, max_scan: int = 8) -> int | None:
    """Find the row listing day-of-month numbers (a run of 1, 2, 3, 4, 5 across columns)."""

    ncols = raw.shape[1]
    for row_idx in range(min(max_scan, len(raw))):
        row = raw.iloc[row_idx]
        for col in range(ncols - 4):
            window = row.iloc[col : col + 5]
            if window.isna().any():
                continue
            try:
                nums = [float(v) for v in window]
            except (TypeError, ValueError):
                continue
            if nums == [1.0, 2.0, 3.0, 4.0, 5.0]:
                return row_idx
    return None


def _sheet_month_year(sheet_name: str, preamble: pd.DataFrame, fallback_year: int) -> tuple[int, int] | None:
    """Infer (year, month) for a report sheet from its title rows or sheet name."""

    texts = [str(v) for v in preamble.to_numpy().ravel() if pd.notna(v)]
    combined = " ".join(texts)
    year_match = re.search(r"(20\d{2})", combined)
    year = int(year_match.group(1)) if year_match else fallback_year
    lowered = combined.lower()
    for name, num in _MONTH_NAME_TO_NUM.items():
        if name in lowered:
            return year, num
    for text in texts:
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.notna(parsed):
            return int(parsed.year), int(parsed.month)
    key = re.sub(r"[^a-z]", "", str(sheet_name).lower())
    if key in _MONTH_ABBR_TO_NUM:
        return year, _MONTH_ABBR_TO_NUM[key]
    return None


def parse_topup_report(raw_bytes: bytes) -> pd.DataFrame:
    """Parse a multi-sheet 'LAPORAN TOP UP' report: one sheet per month, rows per
    server/lokasi, columns per day-of-month. All servers are summed per date."""

    sheets = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=None, header=None)

    headers: dict[str, int] = {}
    found_years: list[int] = []
    for sheet_name, raw in sheets.items():
        header_row = _find_day_header_row(raw)
        if header_row is None:
            continue
        headers[sheet_name] = header_row
        preamble_text = " ".join(str(v) for v in raw.iloc[:header_row].to_numpy().ravel() if pd.notna(v))
        year_match = re.search(r"(20\d{2})", preamble_text)
        if year_match:
            found_years.append(int(year_match.group(1)))
    fallback_year = max(set(found_years), key=found_years.count) if found_years else datetime.now().year

    daily_totals: dict[pd.Timestamp, float] = {}
    for sheet_name, header_row in headers.items():
        raw = sheets[sheet_name]
        month_year = _sheet_month_year(sheet_name, raw.iloc[:header_row], fallback_year)
        if month_year is None:
            continue
        year, month = month_year

        day_columns: dict[int, int] = {}
        for col in range(raw.shape[1]):
            value = raw.iat[header_row, col]
            if pd.notna(value) and float(value).is_integer() and 1 <= float(value) <= 31:
                day_columns[col] = int(value)
        if not day_columns:
            continue

        data = raw.iloc[header_row + 1 :]
        data = data[data[1].notna()]  # drop the trailing grand-total row (blank SERVER name)
        for col, day in day_columns.items():
            try:
                target_date = pd.Timestamp(year=year, month=month, day=day)
            except ValueError:
                continue
            values = pd.to_numeric(data[col], errors="coerce").fillna(0)
            daily_totals[target_date] = daily_totals.get(target_date, 0.0) + float(values.sum())

    if not daily_totals:
        raise DataValidationError("Tidak dikenali sebagai laporan top up (format sheet per bulan, server x hari).")

    frame = pd.DataFrame(
        {"tanggal": list(daily_totals.keys()), "pendapatan": list(daily_totals.values())}
    ).sort_values("tanggal").reset_index(drop=True)
    frame["catatan"] = "gabungan semua server (auto-parse laporan top up)"
    return frame


def read_upload(file_name: str, raw_bytes: bytes) -> pd.DataFrame:
    """Read a CSV/XLSX upload and apply the same strict validation as manual input.

    Plain two-column files (``tanggal``/``pendapatan``, any common header spelling)
    are read directly. If that fails for an Excel file, fall back to the
    multi-sheet "LAPORAN TOP UP" server-by-day report format.
    """

    suffix = Path(file_name).suffix.lower()
    try:
        if suffix == ".csv":
            frame = pd.read_csv(io.BytesIO(raw_bytes))
        elif suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(io.BytesIO(raw_bytes))
        else:
            raise DataValidationError("Format file harus CSV atau Excel (.xlsx).")
    except DataValidationError:
        raise
    except Exception as exc:
        raise DataValidationError(f"File tidak dapat dibaca: {exc}") from exc

    try:
        return validate_entries(frame, source=Path(file_name).name)
    except DataValidationError as simple_error:
        if suffix not in {".xlsx", ".xls"}:
            raise
        try:
            parsed = parse_topup_report(raw_bytes)
        except DataValidationError:
            raise simple_error from None
        return validate_entries(parsed, source=Path(file_name).name)


def find_conflicts(master: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Return incoming rows whose date exists with a different amount."""

    if master.empty or incoming.empty:
        return pd.DataFrame(columns=MASTER_COLUMNS)
    left = master.set_index("tanggal")
    conflicts = incoming[incoming["tanggal"].isin(left.index)].copy()
    conflicts = conflicts[
        conflicts.apply(lambda row: float(left.loc[row["tanggal"], "pendapatan"]) != float(row["pendapatan"]), axis=1)
    ]
    return conflicts.reset_index(drop=True)


def _load_entries_for_dates(conn, dates: list[str]) -> dict[str, dict[str, Any]]:
    """Load current values for a list of dates inside the caller's transaction
    (locked with FOR UPDATE on MariaDB; SQLite already holds the write lock).
    Returns a dict mapping 'YYYY-MM-DD' -> {'pendapatan': ..., 'catatan': ..., 'sumber': ...}"""

    if not dates:
        return {}

    result = {}
    if _is_mariadb_mode():
        cursor = conn.cursor()
        placeholders = ", ".join(["%s"] * len(dates))
        query = f"SELECT tanggal, pendapatan, catatan, sumber FROM entries WHERE tanggal IN ({placeholders}) FOR UPDATE"
        cursor.execute(query, dates)
        for row in cursor.fetchall():
            result[row[0].strftime("%Y-%m-%d")] = {"pendapatan": row[1], "catatan": row[2], "sumber": row[3]}
        cursor.close()
    else:
        placeholders = ", ".join(["?"] * len(dates))
        query = f"SELECT tanggal, pendapatan, catatan, sumber FROM entries WHERE tanggal IN ({placeholders})"
        for row in conn.execute(query, dates).fetchall():
            result[row[0]] = {"pendapatan": row[1], "catatan": row[2], "sumber": row[3]}

    return result


def _apply_row_action(
    date: str,
    incoming_row: dict[str, Any],
    current_value: dict[str, Any] | None,
    action: str,
) -> tuple[dict[str, Any] | None, str, float]:
    """Determine the new value and log action for one row.

    Returns (new_value_dict_or_none, action_taken, log_value_baru).
    If new_value_dict_or_none is None, the row should be deleted.
    """

    old_pendapatan = current_value["pendapatan"] if current_value else None
    new_pendapatan = incoming_row["pendapatan"]

    if current_value is None:
        # New date: always insert
        return (
            {"pendapatan": new_pendapatan, "catatan": incoming_row["catatan"], "sumber": incoming_row["sumber"]},
            "tambah",
            float(new_pendapatan),
        )

    # Existing date: check action
    if float(old_pendapatan) == float(new_pendapatan):
        # Same amount: always skip
        return (current_value, "lewati", float(old_pendapatan))

    if action == "timpa":
        return (
            {"pendapatan": new_pendapatan, "catatan": incoming_row["catatan"], "sumber": incoming_row["sumber"]},
            "timpa",
            float(new_pendapatan),
        )
    elif action == "jumlahkan":
        summed = float(old_pendapatan) + float(new_pendapatan)
        catatan = incoming_row["catatan"] if str(incoming_row["catatan"]).strip() else current_value["catatan"]
        return (
            {"pendapatan": summed, "catatan": catatan, "sumber": incoming_row["sumber"]},
            "jumlahkan",
            summed,
        )
    else:
        # Default: lewati
        return (current_value, "lewati", float(old_pendapatan))


@_retry_on_deadlock
def apply_import(
    master: pd.DataFrame,
    incoming: pd.DataFrame,
    resolutions: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, int]:
    """Merge incoming data with explicit actions for conflicting dates.

    Reads the current database state row-by-row inside a transaction,
    applies resolutions, and writes all changes atomically.

    ``resolutions`` maps an ISO date to ``timpa``, ``jumlahkan``, or ``lewati``.
    New dates are always added. Every row, including skipped conflicts, gets an
    audit record. Returns the updated master dataframe and count of changed rows.
    """

    incoming = validate_entries(incoming, source=None)
    resolutions = resolutions or {}
    logs: list[dict[str, object]] = []
    changed = 0

    # Collect all dates we need to check
    incoming_dates = [row["tanggal"].strftime("%Y-%m-%d") for _, row in incoming.iterrows()]

    # Load current values from DB inside the same (locked) transaction
    ensure_data_files()
    with _connect(write=True) as conn:
        current_values = _load_entries_for_dates(conn, incoming_dates)

        # Process each incoming row and build change list
        changes: list[tuple[str, dict[str, Any] | None]] = []  # (date, new_value_or_None)

        for _, row in incoming.iterrows():
            date = pd.Timestamp(row["tanggal"])
            date_str = date.strftime("%Y-%m-%d")
            current = current_values.get(date_str)
            action = resolutions.get(date_str, "lewati")

            new_value, action_taken, log_value_baru = _apply_row_action(
                date_str,
                {
                    "pendapatan": row["pendapatan"],
                    "catatan": row["catatan"],
                    "sumber": row["sumber"],
                },
                current,
                action,
            )

            if new_value != current or (current is None and new_value is not None):
                changed += 1

            changes.append((date_str, new_value))

            old_val = float(current["pendapatan"]) if current else ""
            logs.append({
                "tanggal data": date,
                "nilai lama": old_val,
                "nilai baru": log_value_baru,
                "sumber": row["sumber"],
                "aksi": action_taken,
            })

        # Write all changes in the same transaction
        if _is_mariadb_mode():
            cursor = conn.cursor()
            # Delete rows that are being removed
            for date_str, new_value in changes:
                if new_value is None:
                    cursor.execute("DELETE FROM entries WHERE tanggal = %s", (date_str,))
            # Upsert rows that are being added or modified
            for date_str, new_value in changes:
                if new_value is not None:
                    cursor.execute(
                        "REPLACE INTO entries (tanggal, pendapatan, catatan, sumber) VALUES (%s, %s, %s, %s)",
                        (date_str, new_value["pendapatan"], new_value["catatan"], new_value["sumber"]),
                    )
            # Insert change log entries
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            log_rows = [
                (
                    record.get("waktu perubahan", timestamp),
                    _value(record.get("tanggal data")),
                    _value(record.get("nilai lama")),
                    _value(record.get("nilai baru")),
                    _value(record.get("sumber")),
                    _value(record.get("aksi")),
                )
                for record in logs
            ]
            query = (
                "INSERT INTO change_log (waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi) "
                "VALUES (%s, %s, %s, %s, %s, %s)"
            )
            cursor.executemany(query, log_rows)
            cursor.close()
        else:
            # SQLite: delete and insert
            for date_str, new_value in changes:
                if new_value is None:
                    conn.execute("DELETE FROM entries WHERE tanggal = ?", (date_str,))
            for date_str, new_value in changes:
                if new_value is not None:
                    conn.execute(
                        "REPLACE INTO entries (tanggal, pendapatan, catatan, sumber) VALUES (?, ?, ?, ?)",
                        (date_str, new_value["pendapatan"], new_value["catatan"], new_value["sumber"]),
                    )
            # Insert change log entries
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            log_rows = [
                (
                    record.get("waktu perubahan", timestamp),
                    _value(record.get("tanggal data")),
                    _value(record.get("nilai lama")),
                    _value(record.get("nilai baru")),
                    _value(record.get("sumber")),
                    _value(record.get("aksi")),
                )
                for record in logs
            ]
            conn.executemany(
                "INSERT INTO change_log (waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                log_rows,
            )

    # Load and return the updated master
    return load_master(), changed


def add_manual_entry(master: pd.DataFrame, entry: dict[str, object], action: str = "tambah") -> tuple[pd.DataFrame, bool]:
    """Add one manual entry, applying an explicit conflict action if needed.
    Returns the updated master dataframe and a boolean (True if changed)."""

    incoming = validate_entries(pd.DataFrame([entry]), source="manual")
    updated_master, changed = apply_import(master, incoming, {incoming.iloc[0]["tanggal"].strftime("%Y-%m-%d"): action})
    return updated_master, changed > 0


@_retry_on_deadlock
def edit_entry(master: pd.DataFrame, date: object, amount: object, note: str = "") -> pd.DataFrame:
    """Edit an entry and record old/new values in the audit log.
    Returns the updated master dataframe read from the database."""

    target = pd.Timestamp(date)
    date_str = target.strftime("%Y-%m-%d")
    new_amount = float(amount)
    if new_amount < 0:
        raise DataValidationError("Pendapatan tidak boleh negatif.")

    ensure_data_files()
    with _connect(write=True) as conn:
        # Load current value
        if _is_mariadb_mode():
            cursor = conn.cursor()
            cursor.execute("SELECT pendapatan FROM entries WHERE tanggal = %s FOR UPDATE", (date_str,))
            result = cursor.fetchone()
            cursor.close()
            if result is None:
                raise DataValidationError("Tanggal yang akan diedit tidak ditemukan.")
            old_amount = result[0]
            # Update the entry
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE entries SET pendapatan = %s, catatan = %s, sumber = %s WHERE tanggal = %s",
                (new_amount, note or "", "manual", date_str),
            )
            cursor.close()
        else:
            cursor = conn.execute("SELECT pendapatan FROM entries WHERE tanggal = ?", (date_str,))
            result = cursor.fetchone()
            if result is None:
                raise DataValidationError("Tanggal yang akan diedit tidak ditemukan.")
            old_amount = result[0]
            # Update the entry
            conn.execute(
                "UPDATE entries SET pendapatan = ?, catatan = ?, sumber = ? WHERE tanggal = ?",
                (new_amount, note or "", "manual", date_str),
            )

        # Log the change
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        log_row = (
            timestamp,
            date_str,
            _value(float(old_amount)),
            _value(new_amount),
            "manual",
            "edit",
        )

        if _is_mariadb_mode():
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO change_log (waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi) VALUES (%s, %s, %s, %s, %s, %s)",
                log_row,
            )
            cursor.close()
        else:
            conn.execute(
                "INSERT INTO change_log (waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi) VALUES (?, ?, ?, ?, ?, ?)",
                log_row,
            )

    return load_master()


@_retry_on_deadlock
def delete_entry(master: pd.DataFrame, date: object) -> pd.DataFrame:
    """Delete one entry while retaining its former value in the audit log.
    Returns the updated master dataframe read from the database."""

    target = pd.Timestamp(date)
    date_str = target.strftime("%Y-%m-%d")

    ensure_data_files()
    with _connect(write=True) as conn:
        # Load current value
        if _is_mariadb_mode():
            cursor = conn.cursor()
            cursor.execute("SELECT pendapatan FROM entries WHERE tanggal = %s FOR UPDATE", (date_str,))
            result = cursor.fetchone()
            cursor.close()
            if result is None:
                raise DataValidationError("Tanggal yang akan dihapus tidak ditemukan.")
            old_amount = result[0]
            # Delete the entry
            cursor = conn.cursor()
            cursor.execute("DELETE FROM entries WHERE tanggal = %s", (date_str,))
            cursor.close()
        else:
            cursor = conn.execute("SELECT pendapatan FROM entries WHERE tanggal = ?", (date_str,))
            result = cursor.fetchone()
            if result is None:
                raise DataValidationError("Tanggal yang akan dihapus tidak ditemukan.")
            old_amount = result[0]
            # Delete the entry
            conn.execute("DELETE FROM entries WHERE tanggal = ?", (date_str,))

        # Log the change
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        log_row = (
            timestamp,
            date_str,
            _value(float(old_amount)),
            "",
            "manual",
            "hapus",
        )

        if _is_mariadb_mode():
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO change_log (waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi) VALUES (%s, %s, %s, %s, %s, %s)",
                log_row,
            )
            cursor.close()
        else:
            conn.execute(
                "INSERT INTO change_log (waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi) VALUES (?, ?, ?, ?, ?, ?)",
                log_row,
            )

    return load_master()


def save_master(frame: pd.DataFrame) -> None:
    """Replace the entries table in one transaction: all-or-nothing, never half-written.
    This is a legacy function kept for CSV migration compatibility."""

    normalized = validate_entries(frame, source=None) if not frame.empty else pd.DataFrame(columns=MASTER_COLUMNS)
    output = normalized.copy()
    if not output.empty:
        output["tanggal"] = output["tanggal"].dt.strftime("%Y-%m-%d")
        output["pendapatan"] = output["pendapatan"].astype(float)
    ensure_data_files()
    with _connect() as conn:
        if _is_mariadb_mode():
            cursor = conn.cursor()
            cursor.execute("DELETE FROM entries")
            if not output.empty:
                for _, row in output.iterrows():
                    cursor.execute(
                        "INSERT INTO entries (tanggal, pendapatan, catatan, sumber) VALUES (%s, %s, %s, %s)",
                        (row["tanggal"], row["pendapatan"], row["catatan"], row["sumber"]),
                    )
            cursor.close()
        else:
            conn.execute("DELETE FROM entries")
            if not output.empty:
                output.to_sql("entries", conn, if_exists="append", index=False)
