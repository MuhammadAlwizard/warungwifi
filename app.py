"""Warung WiFi income dashboard.

Run with: ``streamlit run app.py``
"""

from __future__ import annotations

import math
from io import BytesIO
from datetime import date, datetime

import pandas as pd
import streamlit as st

from modules.analysis import (
    daily_aggregate,
    descriptive_stats,
    extremes,
    filter_dates,
    monthly_aggregate,
    monthly_trend,
    period_change,
    top_bottom_days,
    trend_data,
    yearly_monthly_matrix,
)
from modules.charts import comparison_chart, daily_chart, monthly_chart, monthly_pie, top_bottom_chart, trend_chart, two_month_line_chart, yearly_monthly_chart
from modules.compare import compare_frames, comparison_summary
from modules.data_io import (
    DataValidationError,
    UPLOAD_DIR,
    add_manual_entry,
    apply_import,
    archive_upload,
    delete_entry,
    edit_entry,
    find_conflicts,
    load_change_log,
    load_master,
    read_upload,
    validate_entries,
)


st.set_page_config(page_title="Warung WiFi — Chart Pendapatan", page_icon="📒", layout="wide")

# Jewel-tone theme: deep magenta/wine/amethyst accents on a warm off-white ground — richer and
# darker than a muted pastel, but no black/grey neutrals.
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=Mulish:wght@400;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Mulish', sans-serif; }
h1, h2, h3, [data-testid="stMetricValue"] { font-family: 'Sora', sans-serif; }

[data-testid="stAppViewContainer"] { background: #fff8fa; }
[data-testid="stHeader"] { background: #fff8fa; }

h1 { color: #4a1942; font-weight: 700 !important; }

[data-testid="stMetric"] {
    background: #ffffff;
    border-radius: 10px;
    padding: 14px 16px;
    border: 1px solid #f0dde6;
    border-top: 3px solid #a3195b;
}
[data-testid="stMetricValue"] {
    color: #4a1942;
    font-size: clamp(1rem, 1.6vw, 1.5rem) !important;
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: unset !important;
    line-height: 1.25;
}
[data-testid="stMetricLabel"] { color: #8a5f78; font-weight: 600; }

div.stButton > button, div.stFormSubmitButton > button, div.stDownloadButton > button {
    background: #a3195b;
    color: white;
    border: none;
    border-radius: 10px;
    padding: 0.5rem 1.4rem;
    font-weight: 700;
    transition: background-color 0.15s ease;
}
div.stButton > button:hover, div.stFormSubmitButton > button:hover, div.stDownloadButton > button:hover {
    background: #7d1246;
    color: white;
}

[data-testid="stForm"] {
    background: #ffffff;
    border-radius: 12px;
    padding: 1.2rem;
    border: 1px solid #f0dde6;
}

.stDataFrame { border-radius: 10px; overflow: hidden; }
.stDataFrame thead tr th { background-color: #f3dfe8 !important; color: #4a1942 !important; }

[data-baseweb="tab-list"] { gap: 4px; }

/* shadcn-style sidebar: plain white card, no grey */
[data-testid="stSidebar"] {
    background: #ffffff;
    border-right: 1px solid #f0dde6;
}
[data-testid="stSidebarNav"] a[aria-current="page"] {
    background: #fbe4ee !important;
    color: #a3195b !important;
}

/* shadcn-style bordered cards wrapping stat rows, charts and tables */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid #f0dde6 !important;
    border-radius: 12px !important;
    background: #ffffff;
}
div[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stMetric"] {
    border: none;
    border-radius: 0;
    padding: 4px 8px;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def rupiah(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"Rp {float(value):,.0f}".replace(",", ".")


def _delta_text(pct: float | None) -> str | None:
    if pct is None or not math.isfinite(pct):
        return None
    return f"{pct:+.1f}%"


def export_buttons(frame: pd.DataFrame, stem: str) -> None:
    csv_data = frame.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ Export CSV", csv_data, f"{stem}.csv", "text/csv", key=f"csv_{stem}")
    excel_buffer = BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Data")
    st.download_button("⬇️ Export Excel", excel_buffer.getvalue(), f"{stem}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"xlsx_{stem}")


def show_dashboard(master: pd.DataFrame) -> None:
    st.title("Dashboard Pendapatan Warung WiFi")
    now = pd.Timestamp.now()
    yesterday = now - pd.Timedelta(days=1)
    last_month, last_month_year = (12, now.year - 1) if now.month == 1 else (now.month - 1, now.year)

    month_total = master.loc[(master["tanggal"].dt.year == now.year) & (master["tanggal"].dt.month == now.month), "pendapatan"].sum() if not master.empty else 0
    today_total = master.loc[master["tanggal"].dt.date == now.date(), "pendapatan"].sum() if not master.empty else 0
    yesterday_total = master.loc[master["tanggal"].dt.date == yesterday.date(), "pendapatan"].sum() if not master.empty else 0
    last_month_total = master.loc[(master["tanggal"].dt.year == last_month_year) & (master["tanggal"].dt.month == last_month), "pendapatan"].sum() if not master.empty else 0
    stats = descriptive_stats(master)
    month_extremes = extremes(master, "month")

    with st.container(border=True):
        cols = st.columns(5)
        cols[0].metric("Hari ini", rupiah(today_total), delta=_delta_text(period_change(float(today_total), float(yesterday_total))))
        cols[1].metric("Bulan ini", rupiah(month_total), delta=_delta_text(period_change(float(month_total), float(last_month_total))))
        cols[2].metric("Rata-rata harian", rupiah(stats["mean"]))
        cols[3].metric("Bulan tertinggi", rupiah(month_extremes.get("highest_value") if month_extremes.get("highest") is not None else 0))
        cols[4].metric("Bulan terendah", rupiah(month_extremes.get("lowest_value") if month_extremes.get("lowest") is not None else 0))

    years = sorted(set(master["tanggal"].dt.year.tolist())) if not master.empty else [now.year]
    selected_year = st.selectbox("Tahun chart", years, index=len(years) - 1)
    monthly = monthly_aggregate(master, selected_year)
    month_col, day_col = st.columns(2)
    with month_col:
        with st.container(border=True):
            st.plotly_chart(monthly_chart(monthly, selected_year), use_container_width=True)
            selected_month = st.selectbox("Bulan untuk detail harian", range(1, 13), index=(now.month - 1 if selected_year == now.year else 0), format_func=lambda m: monthly_chart_month_name(m), key="dashboard_month")
    with day_col:
        with st.container(border=True):
            st.plotly_chart(daily_chart(daily_aggregate(master, selected_month, selected_year), selected_month, selected_year), use_container_width=True)
    with st.expander("Lihat kontribusi bulan"):
        st.plotly_chart(monthly_pie(monthly, selected_year), use_container_width=True)

    st.subheader("Perbandingan 2 Bulan")
    month_names = [monthly_chart_month_name(m) for m in range(1, 13)]
    with st.container(border=True):
        compare_cols = st.columns(2)
        with compare_cols[0]:
            year_a = st.selectbox("Tahun A", years, index=0, key="dash_year_a")
            month_a = st.selectbox("Bulan A", range(1, 13), format_func=lambda m: month_names[m - 1], key="dash_month_a")
        with compare_cols[1]:
            year_b = st.selectbox("Tahun B", years, index=len(years) - 1, key="dash_year_b")
            month_b = st.selectbox("Bulan B", range(1, 13), index=min(1, 11), format_func=lambda m: month_names[m - 1], key="dash_month_b")
        label_a = f"{month_names[month_a - 1]} {year_a}"
        label_b = f"{month_names[month_b - 1]} {year_b}"
        st.plotly_chart(
            two_month_line_chart(daily_aggregate(master, month_a, year_a), daily_aggregate(master, month_b, year_b), label_a, label_b),
            use_container_width=True,
        )
        st.caption("Arahkan kursor ke titik tanggal mana pun untuk lihat nilai Bulan A, Bulan B, dan Selisihnya sekaligus — misalnya tanggal 1 Bulan A vs tanggal 1 Bulan B.")


def monthly_chart_month_name(month: int) -> str:
    return ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"][month - 1]


def show_manual(master: pd.DataFrame) -> None:
    st.title("Input Manual")
    tab_single, tab_bulk = st.tabs(["Satu Tanggal", "Banyak Tanggal Sekaligus"])
    with tab_single:
        show_manual_single(master)
    with tab_bulk:
        show_manual_bulk(master)
    st.subheader("Data master")
    show_manage_table(load_master())


def show_manual_single(master: pd.DataFrame) -> None:
    pending = st.session_state.get("pending_manual")
    if pending:
        st.warning(f"Tanggal {pending['tanggal']} sudah memiliki pendapatan {rupiah(pending['lama'])}. Pilih tindakan untuk data baru {rupiah(pending['pendapatan'])}.")
        with st.form("resolve_manual"):
            action = st.radio("Tindakan", ["timpa", "jumlahkan", "lewati"], format_func=lambda x: {"timpa": "Timpa nilai lama", "jumlahkan": "Jumlahkan dengan nilai lama", "lewati": "Lewati data baru"}[x], horizontal=True)
            confirmed = st.form_submit_button("Terapkan")
        if confirmed:
            try:
                updated, _ = add_manual_entry(load_master(), pending, action)
                st.session_state.pop("pending_manual", None)
                st.success("Pilihan konflik sudah diterapkan.")
                st.rerun()
            except DataValidationError as exc:
                st.error(str(exc))
    with st.form("manual_form", clear_on_submit=True):
        entry_date = st.date_input("Tanggal", value=date.today())
        amount = st.number_input("Pendapatan (Rp)", min_value=0.0, step=1000.0, format="%.0f")
        st.caption(f"= {rupiah(amount)}")
        note = st.text_input("Catatan (opsional)")
        submitted = st.form_submit_button("Simpan pendapatan")
    if submitted:
        entry = {"tanggal": entry_date, "pendapatan": amount, "catatan": note}
        try:
            existing = load_master()
            target = pd.Timestamp(entry_date)
            matches = existing[existing["tanggal"] == target]
            if not matches.empty and float(matches.iloc[0]["pendapatan"]) != float(amount):
                st.session_state["pending_manual"] = {**entry, "tanggal": target.strftime("%Y-%m-%d"), "lama": float(matches.iloc[0]["pendapatan"])}
                st.rerun()
            add_manual_entry(existing, entry)
            st.success("Pendapatan tersimpan dan tercatat di log perubahan.")
        except DataValidationError as exc:
            st.error(str(exc))


def show_manual_bulk(master: pd.DataFrame) -> None:
    st.caption("Pilih rentang tanggal (misalnya 1 April - 30 April), isi nominal tiap hari yang mau dicatat, dan biarkan kosong hari yang mau dilewati.")
    range_cols = st.columns(2)
    with range_cols[0]:
        bulk_start = st.date_input("Dari tanggal", value=date.today().replace(day=1), key="bulk_start")
    with range_cols[1]:
        bulk_end = st.date_input("Sampai tanggal", value=date.today(), key="bulk_end")
    if bulk_start > bulk_end:
        st.error("Tanggal awal harus sebelum atau sama dengan tanggal akhir.")
        return
    if (bulk_end - bulk_start).days > 366:
        st.error("Rentang tanggal maksimal 1 tahun sekali input.")
        return

    date_range = pd.date_range(bulk_start, bulk_end, freq="D")
    existing = master.set_index("tanggal") if not master.empty else pd.DataFrame(columns=["pendapatan", "catatan"])
    template = pd.DataFrame({"tanggal": date_range})
    template["pendapatan"] = template["tanggal"].map(existing.get("pendapatan", pd.Series(dtype=float)))
    template["catatan"] = template["tanggal"].map(existing.get("catatan", pd.Series(dtype=str))).fillna("")

    edited = st.data_editor(
        template,
        column_config={
            "tanggal": st.column_config.DateColumn("Tanggal", disabled=True, format="YYYY-MM-DD"),
            "pendapatan": st.column_config.NumberColumn("Pendapatan (Rp)", min_value=0, step=1000, format="%.0f"),
            "catatan": st.column_config.TextColumn("Catatan"),
        },
        hide_index=True,
        use_container_width=True,
        key="bulk_editor",
        num_rows="fixed",
    )
    if st.button("Simpan Semua", type="primary", key="bulk_save"):
        filled = edited.dropna(subset=["pendapatan"])
        if filled.empty:
            st.warning("Belum ada nominal yang diisi.")
            return
        try:
            incoming = validate_entries(filled[["tanggal", "pendapatan", "catatan"]], source="manual (banyak tanggal)")
        except DataValidationError as exc:
            st.error(str(exc))
            return
        resolutions = {row["tanggal"].strftime("%Y-%m-%d"): "timpa" for _, row in incoming.iterrows()}
        _, changed = apply_import(load_master(), incoming, resolutions)
        st.success(f"{changed} tanggal disimpan/diperbarui sekaligus. Baris yang nilainya sama persis otomatis dilewati.")
        st.rerun()


def show_manage_table(master: pd.DataFrame) -> None:
    if master.empty:
        st.info("Belum ada data.")
        return
    view = master.copy()
    view["tanggal"] = view["tanggal"].dt.strftime("%Y-%m-%d")
    view["pendapatan"] = view["pendapatan"].apply(rupiah)
    with st.container(border=True):
        st.dataframe(view, use_container_width=True, hide_index=True)
    st.caption("Edit dan hapus dilakukan satu entri per aksi agar setiap perubahan jelas di audit log.")
    edit_date = st.date_input("Tanggal entri", value=pd.Timestamp(master.iloc[-1]["tanggal"]).date(), key="edit_date")
    found = master[master["tanggal"] == pd.Timestamp(edit_date)]
    if not found.empty:
        current = found.iloc[0]
        edit_col, delete_col = st.columns(2)
        with edit_col:
            with st.form("edit_form"):
                new_amount = st.number_input("Nilai baru (Rp)", min_value=0.0, value=float(current["pendapatan"]), step=1000.0, format="%.0f")
                st.caption(f"= {rupiah(new_amount)}")
                new_note = st.text_input("Catatan baru", value=str(current["catatan"]))
                edit_submit = st.form_submit_button("Simpan edit")
            if edit_submit:
                try:
                    edit_entry(load_master(), edit_date, new_amount, new_note)
                    st.success("Entri diedit dan perubahan dicatat.")
                    st.rerun()
                except DataValidationError as exc:
                    st.error(str(exc))
        with delete_col:
            st.write(f"Entri terpilih: {rupiah(current['pendapatan'])}")
            if st.button("Hapus entri", type="secondary"):
                delete_entry(load_master(), edit_date)
                st.success("Entri dihapus dan perubahan dicatat.")
                st.rerun()


def show_upload(master: pd.DataFrame) -> None:
    st.title("Upload Data")
    st.write("File asli diarsipkan apa adanya di `data/uploads/`. Data baru tidak menimpa tanggal yang bentrok tanpa pilihan Anda.")
    upload = st.file_uploader("Pilih CSV atau Excel", type=["csv", "xlsx"])
    if not upload:
        archives = sorted(UPLOAD_DIR.iterdir(), reverse=True) if UPLOAD_DIR.exists() else []
        st.caption(f"Arsip upload tersimpan: {len([p for p in archives if p.is_file() and p.name != '.gitkeep'])} file")
        return
    raw = upload.getvalue()
    try:
        incoming = read_upload(upload.name, raw)
    except DataValidationError as exc:
        st.error(str(exc))
        return
    st.success(f"{len(incoming)} baris valid.")
    st.dataframe(incoming.assign(tanggal=incoming["tanggal"].dt.strftime("%Y-%m-%d")), use_container_width=True, hide_index=True)
    conflicts = find_conflicts(master, incoming)
    resolutions: dict[str, str] = {}
    if not conflicts.empty:
        st.warning(f"Ditemukan {len(conflicts)} tanggal bentrok. Tentukan tindakan per tanggal.")
        with st.form("upload_merge_form"):
            for _, row in conflicts.iterrows():
                key = row["tanggal"].strftime("%Y-%m-%d")
                old = float(master.loc[master["tanggal"] == row["tanggal"], "pendapatan"].iloc[0])
                resolutions[key] = st.selectbox(f"{key}: lama {rupiah(old)} → baru {rupiah(row['pendapatan'])}", ["timpa", "jumlahkan", "lewati"], key=f"resolution_{upload.name}_{key}")
            import_submit = st.form_submit_button("Arsipkan dan gabungkan")
    else:
        import_submit = st.button("Arsipkan dan tambahkan ke master", type="primary")
    if import_submit:
        try:
            archive_upload(upload.name, raw)
            _, changed = apply_import(load_master(), incoming, resolutions)
            st.success(f"Upload diarsipkan. {changed} perubahan diterapkan; seluruh baris tercatat di change log.")
            st.rerun()
        except DataValidationError as exc:
            st.error(str(exc))


def show_analysis(master: pd.DataFrame) -> None:
    st.title("Analisis")
    if master.empty:
        st.info("Tambahkan data terlebih dahulu.")
        return
    min_date, max_date = master["tanggal"].min().date(), master["tanggal"].max().date()
    selected = st.date_input("Rentang tanggal", value=(min_date, max_date), min_value=min_date, max_value=max_date)
    if isinstance(selected, tuple) and len(selected) == 2:
        filtered = filter_dates(master, selected[0], selected[1])
    else:
        filtered = master.copy()
    stats = descriptive_stats(filtered)
    with st.container(border=True):
        cols = st.columns(6)
        for col, label, key in zip(cols, ["Jumlah hari", "Total", "Rata-rata", "Median", "Minimum", "Maksimum"], ["count", "total", "mean", "median", "min", "max"]):
            col.metric(label, str(stats[key]) if key == "count" else rupiah(stats[key]))
    with st.container(border=True):
        st.plotly_chart(trend_chart(trend_data(filtered)), use_container_width=True)
    trend = trend_data(filtered)
    if len(trend) >= 2:
        latest_change = trend.iloc[-1]["perubahan_pct"]
        st.info(f"Perubahan pada entri terakhir dibanding entri sebelumnya: {latest_change:.2f}%.") if pd.notna(latest_change) else None
    day_extremes = extremes(filtered)
    if day_extremes["highest"] is not None:
        st.write(f"Hari terbaik: **{pd.Timestamp(day_extremes['highest']).strftime('%d %b %Y')}** ({rupiah(day_extremes['highest_value'])}). Hari terendah: **{pd.Timestamp(day_extremes['lowest']).strftime('%d %b %Y')}** ({rupiah(day_extremes['lowest_value'])}).")
    monthly = monthly_trend(filtered)
    if not monthly.empty:
        monthly_values = monthly["pendapatan"]
        st.subheader("Statistik bulanan pada rentang terpilih")
        month_cols = st.columns(5)
        for col, label, value in zip(
            month_cols,
            ["Total", "Rata-rata", "Median", "Minimum", "Maksimum"],
            [monthly_values.sum(), monthly_values.mean(), monthly_values.median(), monthly_values.min(), monthly_values.max()],
        ):
            col.metric(label, rupiah(value))
        if len(monthly) >= 2 and pd.notna(monthly.iloc[-1]["perubahan_pct"]):
            st.info(f"Perubahan bulan terakhir dibanding bulan sebelumnya: {monthly.iloc[-1]['perubahan_pct']:.2f}%.")
        monthly_display = monthly.copy()
        monthly_display["bulan"] = monthly_display["bulan"].astype(str)
        monthly_display["perubahan_pct"] = monthly_display["perubahan_pct"].round(2)
        st.dataframe(monthly_display, use_container_width=True, hide_index=True)
    st.subheader("Tren bulanan antar tahun")
    st.plotly_chart(yearly_monthly_chart(yearly_monthly_matrix(master)), use_container_width=True)

    st.subheader("Hari tertinggi & terendah")
    top_n = st.slider("Jumlah hari ditampilkan (masing-masing sisi)", min_value=3, max_value=10, value=5, key="top_n")
    st.plotly_chart(top_bottom_chart(top_bottom_days(filtered, top_n)), use_container_width=True)

    display = filtered.copy()
    display["tanggal"] = display["tanggal"].dt.strftime("%Y-%m-%d")
    st.dataframe(display, use_container_width=True, hide_index=True)
    export_buttons(display, "hasil_analisis")


def show_compare() -> None:
    st.title("Selisih File")
    st.write("Perbandingan ini berdiri sendiri dan tidak mengubah `master.csv`.")
    file_a = st.file_uploader("File A", type=["csv", "xlsx"], key="compare_a")
    file_b = st.file_uploader("File B", type=["csv", "xlsx"], key="compare_b")
    if not file_a or not file_b:
        st.info("Upload File A dan File B untuk melihat perbandingan.")
        return
    try:
        a = read_upload(file_a.name, file_a.getvalue())
        b = read_upload(file_b.name, file_b.getvalue())
        result = compare_frames(a, b)
    except DataValidationError as exc:
        st.error(str(exc))
        return
    summary = comparison_summary(result)
    cols = st.columns(3)
    cols[0].metric("Total File A", rupiah(summary["total_A"]))
    cols[1].metric("Total File B", rupiah(summary["total_B"]))
    cols[2].metric("Total selisih", rupiah(summary["total_selisih"]))
    st.plotly_chart(comparison_chart(result), use_container_width=True)
    display = result.copy()
    display["tanggal"] = display["tanggal"].dt.strftime("%Y-%m-%d")
    st.dataframe(display, use_container_width=True, hide_index=True)
    export_buttons(display, "hasil_selisih")


def show_history() -> None:
    st.title("Riwayat Perubahan")
    log = load_change_log()
    if log.empty:
        st.info("Belum ada perubahan tercatat.")
        return
    st.dataframe(log, use_container_width=True, hide_index=True)
    export_buttons(log, "change_log")


def main() -> None:
    master = load_master()
    with st.sidebar:
        st.markdown(
            f"<div style='font-family:Sora,sans-serif;font-weight:700;color:#4a1942;"
            f"font-size:1.15rem;padding:8px 0 0;'>📒 Warung WiFi</div>"
            f"<div style='font-family:Mulish,sans-serif;font-weight:600;font-size:0.78rem;"
            f"color:#8a5f78;padding-bottom:6px;'>{len(master)} hari tersimpan</div>",
            unsafe_allow_html=True,
        )
    pages = [
        st.Page(lambda: show_dashboard(master), title="Dashboard", icon="🏠", url_path="dashboard", default=True),
        st.Page(lambda: show_manual(master), title="Input Manual", icon="📝", url_path="input-manual"),
        st.Page(lambda: show_upload(master), title="Upload Data", icon="📤", url_path="upload-data"),
        st.Page(lambda: show_analysis(master), title="Analisis", icon="📈", url_path="analisis"),
        st.Page(show_compare, title="Selisih File", icon="🔍", url_path="selisih-file"),
        st.Page(show_history, title="Riwayat Perubahan", icon="🕘", url_path="riwayat"),
    ]
    nav = st.navigation(pages)
    nav.run()


if __name__ == "__main__":
    main()
