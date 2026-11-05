# Kebutuhan Teknis: Image Upload Service

Dokumen ini merangkum kebutuhan teknis service: apa yang harus dipenuhi sistem, lingkungan tempat ia berjalan, batasan yang berlaku, dan alasan di balik pemilihan tech stack.

Dokumen ini tidak menggantikan dokumen lain. Cakupan dan fase ada di [PLAN.md](PLAN.md); keputusan arsitektur beserta alternatifnya ada di [ARCHITECTURE.md](ARCHITECTURE.md) (ADR-01 s.d. ADR-19). Jika ada perbedaan, kedua dokumen itu yang berlaku.

---

## 1. Ringkasan sistem

Client membuka sesi upload, lalu melakukan streaming hingga 3 gambar (maks. 1 MB per file) dalam satu request. Client memantau progress upload (byte yang diterima) dan progress pemrosesan (pembuatan thumbnail) lewat polling ke satu endpoint. Worker Celery membuat 3 thumbnail WebP untuk setiap gambar. Service juga menyediakan CRUD gambar, auth API key, dan penghapusan metadata EXIF/GPS dari file asli.

Tiga proses yang berjalan:

| Proses | Peran |
|---|---|
| API (uvicorn + FastAPI) | Menerima request, mem-parsing stream, memvalidasi, menyimpan file, mengantrekan task |
| Worker Celery (`--pool=solo`) | Membuat thumbnail dan memperbarui status gambar |
| Client | Membuka sesi, meng-upload, melakukan polling, mengelola gambar |

---

## 2. Lingkungan dan prasyarat

| Item | Kebutuhan | Catatan |
|---|---|---|
| Sistem operasi | Windows 11 | Juga berjalan di Linux/macOS, tetapi target utama adalah Windows |
| Python | **3.14.3** atau lebih baru | Dikunci lewat `.python-version` dan `requires-python` |
| Package manager | `uv` | Instalasi cukup `uv sync` |
| Container | **Tidak ada** (tanpa Docker) | ADR-14 |
| Server eksternal | **Tidak ada** (tanpa Redis, RabbitMQ, PostgreSQL) | Semua penyimpanan memakai SQLite dan disk lokal (ADR-05, ADR-06) |
| Compiler C | **Tidak dibutuhkan** | Semua dependensi punya prebuilt wheel untuk Python 3.14 di Windows |
| Konfigurasi | File `.env` dengan `API_KEYS` terisi | Salin dari `.env.example`; tanpa key, API dan worker menolak start |

---

## 3. Kebutuhan fungsional

| ID | Kebutuhan | Rujukan |
|---|---|---|
| FR-01 | Client membuat sesi upload dengan `POST /uploads` dan menerima `upload_id` sebelum mengirim file | ADR-01 |
| FR-02 | Client mengirim hingga 3 file dalam satu request multipart ke `POST /uploads/{id}/files`. Satu sesi hanya menerima satu request file | ADR-01, ADR-08 |
| FR-03 | Server mem-parsing body sebagai stream dan menghitung byte yang diterima selama upload berlangsung | ADR-02, ADR-03 |
| FR-04 | Setiap file divalidasi sendiri (sukses parsial). File yang ditolak dicatat dengan kode `too_large`, `unsupported_type`, `invalid_image`, `too_many_files`, atau `empty_file` | ADR-08, ADR-10 |
| FR-05 | Metadata (EXIF termasuk GPS, XMP, komentar, chunk teks PNG) dihapus dari file asli sebelum disimpan. Profil ICC dipertahankan | ADR-17 |
| FR-06 | Satu task Celery per gambar membuat thumbnail WebP dengan sisi terpanjang 128, 256, dan 512 px | ADR-04, ADR-11 |
| FR-07 | `GET /uploads/{id}` mengembalikan progress upload (persen byte) dan progress pemrosesan setiap gambar (`current/total`) dalam satu response | ADR-03, ADR-07 |
| FR-08 | CRUD gambar: list berhalaman dengan filter, detail, unduh file asli, unduh thumbnail, ubah `title`/`description`, hapus (baris + file asli + thumbnail) | PLAN §6 |
| FR-09 | Semua endpoint kecuali `/health`, `/docs`, dan `/openapi.json` wajib membawa header `X-API-Key` yang valid | ADR-16 |
| FR-10 | Sesi upload kedaluwarsa 10 menit setelah dibuat. Sesi kedaluwarsa dan file temp basi dibersihkan saat startup | ADR-18 |
| FR-11 | Swagger (`/docs`) mendokumentasikan semua endpoint dan bisa dipakai untuk mencobanya, termasuk upload file dan tombol **Authorize** | ADR-02, ADR-16 |

---

## 4. Kebutuhan non-fungsional

### 4.1 Keamanan

| ID | Kebutuhan | Rujukan |
|---|---|---|
| NFR-S1 | Aplikasi **fail closed**: jika `API_KEYS` kosong, API dan worker menolak start | ADR-16 |
| NFR-S2 | Perbandingan API key memakai `secrets.compare_digest` (tahan *timing attack*). Key tidak pernah di-log | ADR-16 |
| NFR-S3 | Tipe file ditentukan dari isi file (magic bytes + decoding Pillow), bukan dari ekstensi atau header client | ADR-10 |
| NFR-S4 | Gambar dibatasi `max_image_pixels` (40 MP) untuk mencegah *decompression bomb* | ADR-10 |
| NFR-S5 | Nama file dari user tidak pernah dipakai sebagai path. File disimpan dengan nama UUID | ADR-10 |
| NFR-S6 | Request yang `Content-Length`-nya melebihi `3 × 1 MB + 64 KB` ditolak (`413`) sebelum body dibaca | ADR-08 |
| NFR-S7 | Pesan error ke client tidak membocorkan detail internal (path server, pesan Pillow). Detail hanya ada di log | PLAN Fase 6, Fase 8 |

### 4.2 Keandalan dan konsistensi data

| ID | Kebutuhan | Rujukan |
|---|---|---|
| NFR-R1 | File ditulis ke lokasi temp lalu dipindahkan secara atomic (`os.replace`). Tidak ada file setengah jadi di lokasi final | ADR-09 |
| NFR-R2 | Baris `Image` di-commit **sebelum** task dikirim ke Celery | ADR-09 |
| NFR-R3 | Jika client terputus atau terjadi error, semua file temp dari request itu dihapus dan Upload ditandai `failed` | ADR-09 |
| NFR-R4 | Task thumbnail idempoten dan tahan terhadap gambar yang dihapus saat sedang diproses | ADR-04, ADR-09 |
| NFR-R5 | Kolom status di DB adalah sumber kebenaran. Meta Celery hanya untuk `current/total` selama `processing` | ADR-07 |
| NFR-R6 | Akses SQLite bersamaan dari API dan worker tidak menghasilkan `database is locked`: WAL, `busy_timeout=5000`, transaksi singkat, retry task hanya untuk `OperationalError` | ADR-06 |

### 4.3 Responsivitas

| ID | Kebutuhan | Rujukan |
|---|---|---|
| NFR-P1 | Endpoint streaming tidak memblokir event loop. Pekerjaan disk, Pillow, dan DB di dalamnya berjalan lewat `anyio.to_thread.run_sync` | ADR-12 |
| NFR-P2 | Penulisan progress upload di-throttle (tiap ≥ 64 KB atau ≥ 5 %, plus satu penulisan akhir) | ADR-03 |
| NFR-P3 | File yang jelas tidak valid ditolak dengan pengecekan murah lebih dulu, sebelum decoding | ADR-10 |

### 4.4 Testability dan maintainability

| ID | Kebutuhan | Rujukan |
|---|---|---|
| NFR-T1 | Logika inti (validasi, sanitasi, thumbnail, cleanup, parser multipart) berupa fungsi atau class tanpa HTTP dan tanpa Celery, sehingga bisa di-unit-test | ADR-13, ADR-15 |
| NFR-T2 | Test API memakai `TestClient` dan Celery mode eager; tidak butuh server atau worker sungguhan | ADR-15 |
| NFR-T3 | Gambar test dibuat Pillow saat test berjalan. Tidak ada file biner di repo | ADR-15 |
| NFR-T4 | Arah dependensi kode: `api → services → models/storage`; `worker → services`. Worker tidak pernah meng-import app FastAPI | ADR-13 |
| NFR-T5 | Sebuah fase dianggap selesai hanya jika `uv run pytest` hijau | PLAN §8 |

---

## 5. Batasan dan nilai konfigurasi

Semua batas berada di `app/config.py` (`Settings`) dan bisa diubah lewat environment atau `.env`.

| Setting | Nilai default | Fungsi |
|---|---|---|
| `api_keys` | wajib, min. 1 key | Daftar API key, dipisah koma |
| `max_file_bytes` | 1 MB (1.048.576 byte) | Batas ukuran per file |
| `max_files_per_request` | 3 | Batas jumlah file per request |
| `max_request_overhead_bytes` | 64 KB | Ruang untuk boundary dan header multipart dalam batas `413` |
| `upload_session_ttl_seconds` | 600 (10 menit) | Masa berlaku sesi upload |
| `progress_write_min_bytes` / `progress_write_min_fraction` | 64 KB / 5 % | Throttle penulisan progress upload |
| `max_image_pixels` | 40.000.000 | Batas *decompression bomb* |
| `thumbnail_sizes` | `[128, 256, 512]` | Ukuran sisi terpanjang thumbnail |
| `default_page_size` / `max_page_size` | 20 / 100 | Paginasi `GET /images` |
| `celery_result_expires_seconds` | 86.400 (1 hari) | Masa simpan hasil Celery |
| `thumbnail_task_max_retries` | 5 | Retry task saat SQLite terkunci |
| `data_dir` | `data/` | Lokasi DB SQLite dan file gambar |

Tipe file yang diizinkan (JPEG, PNG, GIF, WebP) **bukan** setting, melainkan konstanta di `services/validation.py`, karena setiap format butuh kode sendiri (magic bytes dan cara sanitasi).

---

## 6. Tech stack dan alasan pemilihan

### 6.1 Ringkasan

| Komponen | Versi | Peran |
|---|---|---|
| Python | 3.14.3 | Runtime |
| uv | terbaru | Manajemen dependensi dan virtual environment |
| FastAPI | 0.162 | Framework web, routing, dependency injection, OpenAPI |
| Starlette | 1.10 | Fondasi ASGI di bawah FastAPI (akses `request.stream()`) |
| Uvicorn | 0.54 | Server ASGI |
| Pydantic | 2.14 | Validasi request/response dan skema OpenAPI |
| pydantic-settings | 2.16 | Konfigurasi dari environment / `.env` |
| SQLAlchemy | 2.1 | ORM untuk DB aplikasi; juga dipakai broker dan result backend Celery |
| SQLite | bawaan Python | DB aplikasi, broker Celery, result backend Celery |
| Celery | 5.6 | Antrean task untuk pembuatan thumbnail |
| Kombu | 5.7 | Lapisan transport Celery (transport SQLAlchemy) |
| Pillow | 13 | Verifikasi gambar, penghapusan metadata, pembuatan thumbnail |
| python-multipart | 0.0.33 | Parser multipart level rendah untuk streaming |
| pytest | 9.2 | Test runner |
| httpx | 0.28 | Dibutuhkan `TestClient` FastAPI |

FastAPI, Swagger, Pydantic, dan Celery adalah **stack wajib** dari soal. Komponen lain dipilih untuk melengkapinya di bawah batasan lingkungan (Windows, Python 3.14, tanpa Docker, tanpa Redis).

### 6.2 Alasan per komponen

#### Python 3.14

- **Alasan:** versi yang ditetapkan untuk lingkungan proyek. Semua dependensi sudah dicek punya prebuilt wheel untuk Python 3.14 di Windows, jadi instalasi tidak butuh compiler C.
- **Konsekuensi:** library yang belum punya wheel 3.14 tidak bisa dipakai (contoh: `streaming-form-data`, lihat python-multipart di bawah). Fallback jika ada masalah kompatibilitas: Python 3.13 (ADR-14).

#### uv

- **Alasan:**
  - Satu perintah (`uv sync`) membuat virtual environment, memasang versi Python yang sesuai `.python-version`, dan memasang semua dependensi.
  - `uv.lock` mengunci versi persis setiap dependensi, sehingga reviewer mendapat lingkungan yang sama dengan saat development.
  - `uv run <perintah>` menjalankan perintah di dalam environment proyek tanpa perlu aktivasi manual, yang sering merepotkan di PowerShell.
- **Alternatif:** `pip` + `venv` + `requirements.txt` (tidak ada lockfile yang mengunci dependensi turunan, dan langkah setup lebih banyak); Poetry (lebih lambat dan butuh instalasi terpisah).

#### FastAPI (wajib)

- **Alasan kecocokan:**
  - Swagger UI (`/docs`) dan OpenAPI dibuat otomatis dari kode, sehingga kebutuhan "Swagger bisa dipakai untuk mencoba endpoint" terpenuhi tanpa kerja tambahan untuk endpoint biasa.
  - Terintegrasi langsung dengan Pydantic untuk validasi parameter dan response.
  - Sistem `Depends` membuat auth API key bisa dipasang sekali di level router, sehingga endpoint baru otomatis terlindungi (ADR-16).
  - `fastapi.security.APIKeyHeader` bawaan memunculkan tombol **Authorize** di Swagger tanpa dependensi tambahan.
  - Mendukung `async def` dan `def` dalam satu aplikasi: endpoint streaming bersifat async, endpoint CRUD memakai sync biasa yang dijalankan di threadpool (ADR-12).
- **Catatan:** FastAPI berjalan di atas Starlette. Endpoint upload memakai `request.stream()` milik Starlette secara langsung, karena `UploadFile` membaca seluruh body sebelum kode endpoint berjalan (ADR-02).

#### Uvicorn

- **Alasan:** server ASGI standar untuk FastAPI, sudah termasuk di `fastapi[standard]`, berjalan native di Windows, dan dipakai oleh `fastapi dev` untuk auto-reload saat development.

#### Pydantic v2 (wajib) dan pydantic-settings

- **Alasan kecocokan Pydantic:**
  - Validasi data terstruktur dengan deklarasi, misalnya `Field(max_length=100)` untuk `title` dan `extra="forbid"` pada input PATCH.
  - Skema response menjadi dokumentasi Swagger lengkap dengan contoh payload.
  - `from_attributes=True` mengubah model SQLAlchemy menjadi response tanpa kode konversi manual.
- **Alasan pydantic-settings:**
  - Membaca konfigurasi dari environment dan `.env` dengan tipe yang divalidasi.
  - `Field(min_length=1)` pada `api_keys` membuat aplikasi gagal start jika key kosong (fail closed, ADR-16), tanpa kode pengecekan sendiri.
  - Satu class `Settings` dipakai bersama oleh API dan worker.
- **Batas peran:** Pydantic tidak bisa memeriksa isi biner file. Validasi isi gambar dikerjakan sendiri di `services/validation.py` (ADR-10).

#### SQLAlchemy 2.x dan SQLite

- **Alasan SQLite:**
  - Tidak butuh server, sudah ada di Python, dan cukup untuk skala take-home. Reviewer bisa menjalankan proyek tanpa memasang database apa pun.
  - Dengan WAL dan `busy_timeout`, API dan worker bisa membaca dan menulis bersamaan dengan aman (ADR-06).
- **Alasan SQLAlchemy 2.x:**
  - Mapping bertipe (`Mapped[...]`) dan `select()` gaya 2.0 jelas dibaca dan cocok dengan type hints.
  - Mode **sync** dipakai bersama oleh API dan worker Celery (worker bersifat sync), jadi hanya ada satu cara mengakses DB (ADR-12).
  - Library yang sama juga menjadi dasar transport broker kombu (`sqla+sqlite://`) dan result backend Celery (`db+sqlite://`). Tidak ada dependensi DB tambahan.
  - Pindah ke PostgreSQL di produksi cukup dengan mengganti URL koneksi.
- **Alternatif:** PostgreSQL (butuh server); SQLAlchemy async dengan `aiosqlite` (dependensi tambahan, dan worker Celery tetap butuh versi sync); Alembic (tidak diperlukan karena tabel dibuat dengan `create_all`, ADR-06).

#### Celery 5.6 dan Kombu 5.7 (wajib) dengan broker SQLite

- **Alasan kecocokan Celery:**
  - Memisahkan pembuatan thumbnail dari request HTTP, sehingga response upload tidak menunggu pemrosesan gambar.
  - `update_state(state="PROGRESS", meta=...)` menyediakan progress pemrosesan yang bisa dibaca proses lain lewat `AsyncResult` (ADR-03).
  - Satu task per gambar memberi status, progress, dan retry yang independen (ADR-04).
  - `autoretry_for=(OperationalError,)` menangani SQLite yang sedang terkunci tanpa kode retry sendiri.
- **Alasan broker dan result backend SQLite:**
  - Redis tidak punya build native Windows dan proyek tidak memakai Docker. SQLite tidak butuh server tambahan (ADR-05).
  - Broker dan result backend memakai file terpisah dari `app.db` untuk mengurangi perebutan lock.
- **Alasan `--pool=solo`:** pool default Celery (prefork) tidak berjalan di Windows. `solo` menjalankan satu task dalam satu waktu, cukup untuk cakupan ini (ADR-14).
- **Konsekuensi:** transport SQLAlchemy kombu berstatus experimental dan berbasis polling. Kompatibilitasnya dengan SQLAlchemy 2.1 sudah diverifikasi lewat spike di Fase 0. Untuk produksi, broker diganti ke Redis/RabbitMQ; ini hanya perubahan konfigurasi, bukan kode task.
- **Alternatif:** Redis di WSL, Memurai, RabbitMQ (semuanya butuh server tambahan); `BackgroundTasks` FastAPI (tidak memenuhi stack wajib, tidak punya progress yang bisa dibaca proses lain, dan task hilang jika API restart).

#### Pillow 13

- **Alasan:**
  - Library gambar standar di Python dengan prebuilt wheel untuk Python 3.14 di Windows.
  - Mendukung semua format yang diizinkan (JPEG, PNG, GIF, WebP) untuk dibaca dan ditulis, termasuk WebP untuk thumbnail.
  - Menyediakan semua yang dibutuhkan validasi dan sanitasi tanpa library lain: `verify()` untuk memastikan file benar-benar gambar, `MAX_IMAGE_PIXELS` untuk mencegah *decompression bomb*, `ImageOps.exif_transpose` untuk orientasi, `quality="keep"` untuk menyimpan ulang JPEG tanpa penurunan kualitas berarti, dan `save_all=True` untuk GIF/WebP animasi (ADR-10, ADR-11, ADR-17).
  - Format gambar dideteksi dari isi file, sehingga `content_type` yang disimpan tidak bergantung pada header client.
- **Alternatif:** pyvips atau Wand/ImageMagick (lebih cepat untuk gambar besar, tetapi butuh library native terpisah di Windows; gambar di proyek ini maks. 1 MB, jadi kecepatan tambahan tidak diperlukan).

#### python-multipart 0.0.33

- **Alasan:**
  - `MultipartParser` level rendahnya bekerja dengan callback per chunk, sehingga file bisa divalidasi dan dibatasi ukurannya **selama** upload, dan byte yang diterima bisa dihitung untuk progress (ADR-02).
  - Murni Python dan sudah menjadi dependensi FastAPI, jadi tidak menambah dependensi baru.
  - Parser bersifat sync dan tanpa I/O, sehingga `MultipartStreamHandler` bisa di-unit-test dengan body buatan dan ukuran chunk acak.
- **Alternatif:** `UploadFile` FastAPI (seluruh body dibaca dulu, jadi progress upload tidak bisa diukur); `streaming-form-data` (tidak punya wheel Python 3.14 untuk Windows dan butuh compiler C).

#### pytest dan httpx

- **Alasan:**
  - pytest: fixture memudahkan folder data dan DB temp per test, Settings test, dan gambar buatan Pillow (ADR-15).
  - httpx: dibutuhkan oleh `TestClient` FastAPI untuk menguji endpoint tanpa menjalankan server.
- Celery dijalankan dalam mode eager di test, sehingga test tidak butuh broker atau worker.

### 6.3 Yang sengaja tidak dipakai

| Tidak dipakai | Alasan |
|---|---|
| Docker | Batasan lingkungan proyek (ADR-14) |
| Redis / RabbitMQ | Butuh server tambahan; tidak ada build native Windows untuk Redis (ADR-05) |
| PostgreSQL | Butuh server; SQLite cukup untuk cakupan ini (ADR-06) |
| Alembic | Tabel dibuat dengan `create_all`; perubahan skema = hapus DB lokal (ADR-06) |
| `aiosqlite` / SQLAlchemy async | Worker Celery tetap sync; satu cara akses DB lebih sederhana (ADR-12) |
| `streaming-form-data` | Tidak ada wheel Python 3.14 untuk Windows (ADR-02) |
| SSE / WebSocket | Polling lebih sederhana dan bisa dicoba dari Swagger (ADR-03) |
| `slowapi` / rate limiter | Ditunda ke produksi bersama Redis atau reverse proxy (ADR-19) |
| Library auth (JWT, OAuth) | API key dengan `APIKeyHeader` bawaan FastAPI sudah cukup (ADR-16) |

---

## 7. Kriteria penerimaan

1. `uv sync` berhasil di Windows 11 dengan Python 3.14 tanpa compiler C.
2. `uv run pytest` hijau.
3. API berjalan dengan `uv run fastapi dev main.py`, dan `/docs` bisa dibuka tanpa API key.
4. Worker berjalan dengan `uv run celery -A app.worker.celery_app worker --pool=solo -l info` dan memproses task dari broker SQLite.
5. Alur lengkap bisa dijalankan lewat Swagger atau curl: buat sesi → upload hingga 3 file → polling hingga semua gambar `done` → unduh file asli (tanpa EXIF/GPS) dan thumbnail → ubah dan hapus gambar.
6. Request tanpa API key yang valid ke endpoint terlindungi mendapat `401`.
