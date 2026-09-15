# Plan: Warung WiFi — Chart Pendapatan

## 1. Ringkasan
Aplikasi untuk mencatat, menganalisis, dan memvisualisasikan pendapatan warung wifi secara **harian** dan **bulanan**, dengan pengolahan data pakai **pandas** supaya data konsisten, tervalidasi, dan setiap perubahan bisa dilacak (tidak ada data yang berubah/hilang diam-diam). Coding dikerjakan oleh Codex berdasarkan plan ini.

## 2. Fitur Utama
1. **Input manual** — form untuk menambahkan pendapatan per tanggal (tanggal, nominal, catatan opsional).
2. **Upload file** — import data dari CSV/Excel yang sudah direkap sebelumnya, diproses & divalidasi pakai pandas.
3. **Chart harian** — menampilkan pendapatan per hari dalam rentang/bulan yang dipilih.
4. **Chart bulanan** — menampilkan total pendapatan per bulan (agregasi otomatis dari data harian, pakai `groupby` pandas).
5. **Ringkasan (summary cards)** — total hari ini, total bulan ini, rata-rata harian, bulan tertinggi/terendah.
6. **Modul Analisis** — statistik deskriptif & tren pendapatan (lihat bagian 6).
7. **Modul Selisih File** — bandingkan 2 file upload (misal bulan A vs bulan B) dan tampilkan perbedaannya (lihat bagian 7).
8. **Filter rentang tanggal / bulan / tahun.**
9. **Edit & hapus entri**, tapi setiap perubahan tercatat di log — data mentah asli tidak pernah ditimpa langsung (lihat bagian 3).

## 3. Sumber Data & Integritas Data
- **Input manual**: form (tanggal, nominal, catatan) → divalidasi (tipe data, format tanggal, nominal ≥ 0) via pandas sebelum disimpan.
- **Upload file — format sederhana**: CSV atau Excel dengan kolom `tanggal, pendapatan` (nama kolom fleksibel — dikenali otomatis dari sinonim umum seperti `Tanggal/Date/Tgl` dan `Pendapatan/Total/Nominal/Jumlah/Omzet`, tidak case-sensitive).
- **Upload file — format "Laporan Top Up"**: kalau file punya banyak sheet (1 sheet/bulan) dengan baris = server/lokasi dan kolom = hari-ke-1..31 (format rekap yang umum dipakai warung wifi multi-server), sistem otomatis mendeteksi dan mem-parsing format ini (`modules/data_io.py::parse_topup_report`), menjumlahkan semua server per tanggal jadi satu angka pendapatan harian. Dipakai sebagai fallback kalau format sederhana gagal dibaca.

**Prinsip integritas data (biar "ga diubah/gajelas"):**
- File mentah yang diupload **disimpan apa adanya** di `data/uploads/` (tidak pernah ditimpa) — jadi selalu bisa ditelusuri balik ke sumber aslinya.
- Data gabungan disimpan di **SQLite** (`data/warungwifi.db`, tabel `entries`) — hanya diubah lewat proses merge terkontrol pakai pandas + transaksi SQLite (replace atomik: berhasil semua atau gagal semua, tidak pernah ada kondisi setengah-tertulis), bukan ditulis manual. Ini gantiin pendekatan CSV awal supaya data pribadi lebih aman dari risiko corrupt kalau proses keganggu di tengah jalan (mati listrik/freeze).
- Setiap kali ada data baru masuk (manual/upload) yang bentrok dengan data lama (tanggal sama, nilai beda), sistem **tidak langsung menimpa** — user diberi pilihan (timpa / jumlahkan / lewati), dan aksi yang dipilih dicatat di tabel `change_log` (kolom: waktu perubahan, tanggal data, nilai lama, nilai baru, sumber, aksi).
- Edit/hapus manual dari tabel juga tercatat di `change_log` yang sama.
- Data pre-SQLite (CSV lama) dimigrasi otomatis sekali jalan ke database saat aplikasi pertama kali dijalankan dengan versi baru; file CSV lama disimpan sebagai backup (`.csv.migrated`), tidak dihapus.

### Struktur data (per entri, disimpan di tabel `entries`)
| kolom | tipe | keterangan |
|---|---|---|
| tanggal | date | wajib, unik per hari |
| pendapatan | float | nominal, ≥ 0 |
| catatan | string | opsional |
| sumber | string | `manual` / nama file upload |

## 4. Modul Analisis
Menggunakan pandas untuk menghasilkan insight, bukan cuma angka mentah:
- **Statistik deskriptif**: total, rata-rata, median, min, max pendapatan (harian & bulanan).
- **Tren**: kenaikan/penurunan pendapatan dibanding periode sebelumnya (`pct_change()` pandas) — hari ini vs kemarin, bulan ini vs bulan lalu.
- **Hari/bulan terbaik & terburuk**: otomatis terdeteksi dari data (`idxmax`/`idxmin`).
- **Moving average** (opsional): rata-rata bergerak 7 hari untuk melihat tren tanpa noise harian.
- Semua hasil analisis ditampilkan sebagai teks ringkas + tabel, mendampingi chart (bukan menggantikan).

## 5. Modul Selisih File (Perbandingan 2 File)
- User upload **File A** dan **File B** (misal rekap bulan Agustus vs September).
- Sistem gabungkan berdasarkan `tanggal` (`pandas.merge` dengan `outer join`) dan hitung kolom `selisih = pendapatan_B - pendapatan_A`.
- Tampilkan:
  - Tabel perbandingan per tanggal (pendapatan A, pendapatan B, selisih, % perubahan).
  - Ringkasan: total selisih, tanggal dengan selisih terbesar (naik/turun).
  - Chart selisih (bar chart, warna beda untuk naik vs turun).
- Ini murni fitur "bandingkan 2 file", terpisah dari proses input data ke master (tidak otomatis mengubah tabel `entries`).

## 6. Dashboard Utama — 2 Chart Terpisah
Halaman Dashboard **wajib punya 2 chart yang berdiri sendiri-sendiri**, bukan digabung jadi satu:

1. **Chart Bulanan** (atas / kiri)
   - Input: pilih **tahun** (dropdown).
   - Data: total pendapatan **12 bulan** (Jan–Des) untuk tahun itu, hasil `groupby` per bulan dari tabel `entries`. Bulan yang belum ada datanya tetap tampil dengan nilai 0 (bukan bulan-nya hilang dari chart).
   - Tipe: bar/column chart, 12 batang berjajar.

2. **Chart Harian** (bawah / kanan)
   - Input: pilih **bulan & tahun** (dropdown, default = bulan yang sedang dipilih di Chart Bulanan — misal klik/pilih "Maret" di chart bulanan langsung filter chart harian ke Maret).
   - Data: pendapatan per hari (tanggal 1 s.d. akhir bulan) untuk bulan itu.
   - Tipe: bar chart atau line chart (tren harian).

Keduanya tampil bersamaan di satu layar (misal 2 kolom atau atas-bawah pakai `st.columns`/berurutan), supaya user bisa lihat gambaran tahunan (bulanan) dan detail harian sekaligus tanpa pindah menu.

## 7. Jenis Chart Lainnya
| Chart | Tujuan |
|---|---|
| Line chart | Tren pendapatan harian (+ moving average opsional), di menu Analisis |
| Donut/pie chart (opsional) | Kontribusi tiap bulan terhadap total tahun berjalan |
| Bar chart (selisih) | Perbandingan 2 file — naik/turun per tanggal, di menu Selisih File |
| Summary cards | Total hari ini, total bulan ini, rata-rata, bulan tertinggi/terendah |

## 7. Rekomendasi Tech Stack
Karena butuh pandas untuk pengolahan & validasi data, rekomendasi pindah dari vanilla JS ke Python:

**Opsi utama (disarankan): Streamlit + pandas + Plotly**
- Satu aplikasi Python — tidak perlu pisah frontend/backend, cocok untuk aplikasi berbasis data & analisis.
- `pandas` untuk semua pengolahan, validasi, agregasi, dan perbandingan file.
- `plotly` (via `st.plotly_chart`) untuk chart interaktif (bar, line, pie/donut).
- `openpyxl` untuk baca/tulis file Excel.
- Data tersimpan di **SQLite lokal** (`data/warungwifi.db`) yang dikelola lewat pandas — tetap file lokal tanpa perlu setup server database, tapi tulisnya transaksional (aman dari corrupt kalau proses keganggu di tengah jalan).
- Kekurangan: butuh Python terinstall & dijalankan lewat `streamlit run app.py` (tidak sekadar buka file HTML di browser).

**Opsi alternatif: Python backend (Flask/FastAPI) + halaman web terpisah**
- Backend Python+pandas untuk proses data & analisis (expose sebagai API).
- Frontend HTML/JS terpisah (Chart.js) untuk tampilan.
- Lebih fleksibel & bisa di-deploy sebagai web biasa, tapi lebih banyak bagian yang harus dikerjakan Codex.

→ **Rekomendasi: Streamlit.** Paling cepat dibangun, pandas jadi warga kelas satu (bukan tempelan), dan cocok untuk kebutuhan analisis + perbandingan file. Kalau nanti butuh dipakai banyak orang sekaligus lewat browser publik tanpa install apa-apa, baru pertimbangkan opsi Flask/FastAPI.

## 8. Struktur File (Streamlit)
```
warungwifi_chart/
├── app.py                  # entry point Streamlit (halaman utama, navigasi antar menu)
├── data/
│   ├── warungwifi.db       # SQLite: tabel `entries` (data pendapatan) + `change_log` (audit trail)
│   ├── *.csv.migrated      # backup CSV lama, hasil migrasi otomatis satu kali ke SQLite
│   └── uploads/             # arsip file mentah yang pernah diupload (read-only, untuk audit)
├── modules/
│   ├── data_io.py          # koneksi & baca/tulis SQLite, validasi, logging perubahan, migrasi CSV lama
│   ├── analysis.py         # statistik deskriptif, tren, moving average
│   ├── compare.py          # logika selisih 2 file
│   └── charts.py           # fungsi pembuat chart Plotly
├── requirements.txt         # streamlit, pandas, plotly, openpyxl (sqlite3 bawaan Python)
└── PLAN.md                  # dokumen ini
```

## 9. Alur Kerja Aplikasi
1. Jalankan `streamlit run app.py` → tampil Dashboard: summary cards + **Chart Bulanan (12 bulan)** dan **Chart Harian** berdampingan (lihat bagian 6), keduanya dari tabel `entries`.
2. Menu **Input Manual** → isi form → divalidasi → ditambahkan ke `entries` (transaksi SQLite) → dicatat di `change_log` → dashboard refresh.
3. Menu **Upload Data** → upload CSV/Excel → file mentah diarsipkan di `data/uploads/` → divalidasi → kalau ada bentrok data, user pilih aksi (timpa/jumlahkan/lewati) → digabung ke `entries` → dicatat di log.
4. Menu **Analisis** → pilih rentang tanggal/bulan → tampil statistik, tren, moving average.
5. Menu **Selisih File** → upload File A & File B → tampil tabel & chart perbandingan (tidak menyentuh tabel `entries`).
6. Menu **Riwayat Perubahan** → tampilkan isi tabel `change_log` biar transparan, data apa yang pernah diubah, kapan, dan dari sumber mana.

## 10. Langkah Implementasi (untuk Codex)
1. Setup project: `requirements.txt` (streamlit, pandas, plotly, openpyxl), struktur folder seperti di atas.
2. `modules/data_io.py`:
   - Koneksi SQLite (`data/warungwifi.db`) dengan `journal_mode=WAL` + `synchronous=FULL`, satu transaksi per operasi tulis (commit/rollback) supaya tidak pernah ada data setengah-tertulis.
   - Buat tabel `entries` dan `change_log` kalau belum ada; migrasi otomatis satu-kali dari CSV lama kalau ketemu dan tabel masih kosong.
   - Fungsi validasi entri (tipe data, tanggal, nominal ≥ 0).
   - Fungsi tambah/edit/hapus entri, yang selalu menulis ke `change_log`.
   - Fungsi import file upload → arsipkan ke `data/uploads/`, validasi, deteksi bentrok dengan master.
3. `modules/analysis.py`: fungsi statistik deskriptif, `pct_change`, moving average, deteksi hari/bulan ekstrem.
4. `modules/compare.py`: fungsi merge 2 dataframe by tanggal, hitung selisih & % perubahan.
5. `modules/charts.py`: fungsi-fungsi pembuat chart Plotly, termasuk `chart_bulanan(df, tahun)` (12 batang Jan–Des, isi 0 kalau kosong) dan `chart_harian(df, bulan, tahun)` (1 batang/hari untuk bulan itu), plus line tren, pie kontribusi, bar selisih.
6. `app.py`: navigasi antar menu (`st.sidebar` atau `st.tabs`) — Dashboard (2 chart berdampingan pakai `st.columns`, dengan dropdown tahun & bulan), Input Manual, Upload Data, Analisis, Selisih File, Riwayat Perubahan.
7. Testing manual: input data dummy, upload contoh CSV dengan beberapa bentrok tanggal, upload 2 file untuk uji selisih, cek tabel `change_log` mencatat semua perubahan dengan benar.

## 11. Catatan Tambahan
- Format nominal: tampilkan dalam Rupiah (contoh: `Rp 150.000`).
- Validasi ketat di titik masuk data (manual & upload) — setelah masuk ke tabel `entries`, data dianggap sudah bersih.
- Karena data disimpan di SQLite lokal (bukan localStorage browser), data konsisten walau diakses ulang dari sesi berbeda, tahan dari corrupt kalau proses keganggu di tengah tulis, dan bisa di-backup dengan copy file `data/warungwifi.db`.
- Sediakan tombol "Export hasil analisis/selisih ke CSV/Excel" biar bisa dipakai di luar aplikasi juga.
