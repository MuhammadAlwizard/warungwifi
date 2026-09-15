"""Data storage, validation, imports, and audit logging.

The application keeps the original upload bytes in ``data/uploads`` and stores
normalized entries plus the change log in a local SQLite database
(``data/warungwifi.db``). Every write goes through one transaction, so a crash
or power loss mid-write leaves the previous data intact instead of a half-written
file — the failure mode a plain CSV can't protect against.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

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


@contextmanager
def _connect():
    """One transaction per call: commits on success, rolls back on any error."""

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_data_files() -> None:
    """Create the data directory and SQLite tables, then migrate any pre-SQLite CSVs."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
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
        frame = pd.read_sql_query("SELECT tanggal, pendapatan, catatan, sumber FROM entries", conn)
    if frame.empty:
        return pd.DataFrame(columns=MASTER_COLUMNS).astype({"tanggal": "datetime64[ns]", "pendapatan": "float64"})
    return validate_entries(frame, source=None)


def save_master(frame: pd.DataFrame) -> None:
    """Replace the entries table in one transaction: all-or-nothing, never half-written."""

    normalized = validate_entries(frame, source=None) if not frame.empty else pd.DataFrame(columns=MASTER_COLUMNS)
    output = normalized.copy()
    if not output.empty:
        output["tanggal"] = output["tanggal"].dt.strftime("%Y-%m-%d")
        output["pendapatan"] = output["pendapatan"].astype(float)
    ensure_data_files()
    with _connect() as conn:
        conn.execute("DELETE FROM entries")
        if not output.empty:
            output.to_sql("entries", conn, if_exists="append", index=False)


def load_change_log() -> pd.DataFrame:
    ensure_data_files()
    with _connect() as conn:
        frame = pd.read_sql_query(
            "SELECT waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi FROM change_log ORDER BY id",
            conn,
        )
    frame.columns = LOG_COLUMNS
    return frame.fillna("")


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
        conn.executemany(
            "INSERT INTO change_log (waktu_perubahan, tanggal_data, nilai_lama, nilai_baru, sumber, aksi) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def archive_upload(file_name: str, raw_bytes: bytes) -> Path:
    """Archive upload bytes without overwriting an earlier raw file."""

    ensure_data_files()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file_name).name).strip("._") or "upload"
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    destination = UPLOAD_DIR / f"{stamp}_{safe_name}"
    destination.write_bytes(raw_bytes)
    return destination


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


def _replace_row(master: pd.DataFrame, date: pd.Timestamp, row: pd.Series) -> pd.DataFrame:
    result = master[master["tanggal"] != date].copy()
    return pd.concat([result, pd.DataFrame([row[MASTER_COLUMNS].to_dict()])], ignore_index=True)


def apply_import(
    master: pd.DataFrame,
    incoming: pd.DataFrame,
    resolutions: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, int]:
    """Merge incoming data with explicit actions for conflicting dates.

    ``resolutions`` maps an ISO date to ``timpa``, ``jumlahkan``, or ``lewati``.
    New dates are always added.  Every row, including skipped conflicts, gets an
    audit record.
    """

    current = master.copy()
    incoming = validate_entries(incoming, source=None)
    resolutions = resolutions or {}
    logs: list[dict[str, object]] = []
    changed = 0

    for _, row in incoming.iterrows():
        date = pd.Timestamp(row["tanggal"])
        key = date.strftime("%Y-%m-%d")
        matches = current.index[current["tanggal"] == date].tolist()
        if not matches:
            current = pd.concat([current, pd.DataFrame([row[MASTER_COLUMNS].to_dict()])], ignore_index=True)
            changed += 1
            logs.append({"tanggal data": date, "nilai lama": "", "nilai baru": row["pendapatan"], "sumber": row["sumber"], "aksi": "tambah"})
            continue

        index = matches[0]
        old_amount = current.at[index, "pendapatan"]
        action = resolutions.get(key, "lewati")
        if float(old_amount) == float(row["pendapatan"]):
            action = "lewati"
        if action == "timpa":
            current = _replace_row(current, date, row)
            changed += 1
            new_amount = row["pendapatan"]
        elif action == "jumlahkan":
            current.at[index, "pendapatan"] = float(old_amount) + float(row["pendapatan"])
            if str(row["catatan"]).strip():
                current.at[index, "catatan"] = row["catatan"]
            current.at[index, "sumber"] = row["sumber"]
            changed += 1
            new_amount = current.at[index, "pendapatan"]
        else:
            action = "lewati"
            new_amount = old_amount
        logs.append({"tanggal data": date, "nilai lama": old_amount, "nilai baru": new_amount, "sumber": row["sumber"], "aksi": action})

    current = current.sort_values("tanggal").reset_index(drop=True)
    if changed:
        save_master(current)
    append_change_logs(logs)
    return current, changed


def add_manual_entry(master: pd.DataFrame, entry: dict[str, object], action: str = "tambah") -> tuple[pd.DataFrame, bool]:
    """Add one manual entry, applying an explicit conflict action if needed."""

    incoming = validate_entries(pd.DataFrame([entry]), source="manual")
    return apply_import(master, incoming, {incoming.iloc[0]["tanggal"].strftime("%Y-%m-%d"): action})


def edit_entry(master: pd.DataFrame, date: object, amount: object, note: str = "") -> pd.DataFrame:
    """Edit an entry and record old/new values in the audit log."""

    target = pd.Timestamp(date)
    new_amount = float(amount)
    if new_amount < 0:
        raise DataValidationError("Pendapatan tidak boleh negatif.")
    current = master.copy()
    matches = current.index[current["tanggal"] == target].tolist()
    if not matches:
        raise DataValidationError("Tanggal yang akan diedit tidak ditemukan.")
    index = matches[0]
    old_amount = current.at[index, "pendapatan"]
    current.at[index, "pendapatan"] = new_amount
    current.at[index, "catatan"] = note or ""
    current.at[index, "sumber"] = "manual"
    save_master(current)
    append_change_logs([{"tanggal data": target, "nilai lama": old_amount, "nilai baru": new_amount, "sumber": "manual", "aksi": "edit"}])
    return load_master()


def delete_entry(master: pd.DataFrame, date: object) -> pd.DataFrame:
    """Delete one entry while retaining its former value in the audit log."""

    target = pd.Timestamp(date)
    current = master.copy()
    matches = current.index[current["tanggal"] == target].tolist()
    if not matches:
        raise DataValidationError("Tanggal yang akan dihapus tidak ditemukan.")
    index = matches[0]
    old_amount = current.at[index, "pendapatan"]
    current = current.drop(index).reset_index(drop=True)
    save_master(current)
    append_change_logs([{"tanggal data": target, "nilai lama": old_amount, "nilai baru": "", "sumber": "manual", "aksi": "hapus"}])
    return load_master()
