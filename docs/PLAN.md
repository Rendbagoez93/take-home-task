# Image Upload Service: Rencana Implementasi

## 1. Tujuan

Service FastAPI tempat client membuka sesi upload, lalu melakukan streaming hingga 3 gambar per request. Client cukup melakukan polling ke satu endpoint untuk memantau dua hal:

1. **Progress upload**: jumlah byte yang sudah diterima server.
2. **Progress pemrosesan**: satu task Celery per gambar yang membuat 3 thumbnail.

Gambar memiliki endpoint CRUD, dan setiap upload divalidasi. Semua endpoint dilindungi API key. Swagger (`/docs`) mendokumentasikan semua endpoint dan bisa dipakai untuk mencobanya.

## 2. Keputusan (sudah disepakati)

| Topik | Keputusan |
|---|---|
| Progress | Progress upload (byte) dan pemrosesan (Celery), dikirim lewat **polling** |
| ID progress | **Dua langkah**: `POST /uploads` mengembalikan `upload_id`, lalu `POST /uploads/{id}/files` |
| Pekerjaan Celery | 3 ukuran thumbnail per gambar (sisi terpanjang 128, 256, 512 px; bisa dikonfigurasi) |
| Broker / backend Celery | **Hanya SQLite**: transport SQLAlchemy dari kombu + DB result backend Celery |
| DB aplikasi | SQLite melalui SQLAlchemy 2.x |
| Penyimpanan | Disk lokal |
| File per request | **Maks. 3**. Bagian file setelah file ke-3 dalam request yang sama ditolak saat streaming. Request berikutnya tidak dibatasi |
| Aturan multi-file | **Sukses parsial**: setiap file punya status sendiri |
| Batas ukuran | **1 MB per file** |
| Task | **Satu task Celery per gambar** |
| HTML demo / Docker | Di luar cakupan |
| Auth | **API key** lewat header `X-API-Key`, wajib di semua endpoint kecuali `/health` dan dokumentasi |
| Privasi file asli | **Metadata (EXIF, termasuk GPS) dihapus** dari file asli sebelum disimpan |
| Sesi terbengkalai | **Sesi kedaluwarsa 10 menit** setelah dibuat; **pembersihan saat startup** |
| Rate limiting & CORS | **Hanya didokumentasikan** sebagai pekerjaan produksi (ADR-19 di ARCHITECTURE.md) |

**Asumsi:** setiap sesi upload hanya menerima **satu** request file. `POST /uploads/{id}/files` kedua pada sesi yang sama mengembalikan `409`, karena dua request akan mencampur hitungan byte-nya. Untuk upload lagi, client membuka sesi baru (biayanya kecil).

**Asumsi keamanan:** API key tidak memiliki konsep kepemilikan. Semua key yang valid bisa mengakses semua gambar. Kepemilikan per key dicatat sebagai pengembangan berikutnya.

## 3. Stack (sudah di-resolve dan dicek di Python 3.14 / Windows, semuanya punya prebuilt wheel)

`fastapi 0.162` · `starlette 1.10` · `pydantic 2.14` + `pydantic-settings` · `sqlalchemy 2.1` · `celery 5.6` / `kombu 5.7` · `pillow 13` · `python-multipart 0.0.33` · `uvicorn 0.54` · `pytest 9` + `httpx`

`streaming-form-data` tidak dipakai: tidak ada wheel Python 3.14 untuk Windows sehingga butuh compiler C. Sebagai gantinya kita memakai `MultipartParser` level rendah dari `python-multipart`.

Auth API key memakai `fastapi.security.APIKeyHeader` bawaan FastAPI, jadi tidak perlu dependensi tambahan.

## 4. Arsitektur

```
  Semua request (kecuali /health, /docs, /openapi.json) wajib membawa header X-API-Key.

                 POST /uploads ──► buat baris Upload (status=created, expires_at=+10 menit) ──► {upload_id}

Client ── POST /uploads/{id}/files (raw stream) ──► FastAPI (async)
            │                                         │ callback MultipartParser, per bagian:
            │                                         │   header → cek ekstensi/content-type
            │                                         │   byte pertama → cek magic bytes
            │                                         │   data → file temp, batas 1 MB per file
            │                                         │   akhir bagian → Pillow verify → hapus metadata → atomic move
            │                                         │ tiap ~64 KB → Upload.bytes_received (di-throttle)
            │                                         │ file diterima → baris Image COMMIT → task.delay()
            ◄── 202 {hasil per file} ─────────────────┘                          │
                                                                                 ▼
Client ── GET /uploads/{id} (poll) ──► FastAPI ◄── results.db ◄── worker Celery (--pool=solo)
            ◄── {% byte, images:[{status, current/total}], rejected:[…]}    │ exif_transpose
                                                                            │ thumbnail ×3, update_state
                                                                            │ Image.status=done/failed
File SQLite:  data/app.db (WAL)   data/celery-broker.db   data/celery-results.db
Disk:         data/uploads/originals/<uuid>.<ext>   data/uploads/thumbnails/<uuid>_<size>.webp
```

### Struktur kode

```
main.py                 (di root repo) app factory FastAPI, router, exception handler, lifespan startup (create_all, folder, cleanup)
app/
  config.py             Settings (pydantic-settings): path, batas, ukuran, URL DB, API_KEYS, masa berlaku sesi
  security.py           require_api_key: dependency FastAPI untuk header X-API-Key
  db.py                 engine (WAL + busy_timeout + check_same_thread=False), SessionLocal, get_db
  models.py             Upload, Image (mapping bertipe SQLAlchemy 2.x)
  schemas.py            model request/response Pydantic
  api/
    uploads.py          POST /uploads, POST /uploads/{id}/files, GET /uploads/{id}
    images.py           CRUD + download file/thumbnail
  services/
    validation.py       pengecekan murni: ekstensi, content-type, magic bytes, Pillow verify. Daftar tipe yang diizinkan berupa konstanta di sini, bukan Settings, karena setiap format butuh kode sendiri (magic bytes, sanitize §7)
    sanitize.py         murni: strip_metadata(path, format): hapus EXIF/GPS/komentar dari file asli
    storage.py          path temp, atomic move, hapus original+thumbnail
    cleanup.py          cleanup_abandoned(now): hapus sesi kedaluwarsa tanpa gambar + file temp basi
    multipart_stream.py MultipartStreamHandler: sync, digerakkan endpoint, menghasilkan hasil per bagian
    upload_service.py   orkestrasi stream → validasi → simpan → enqueue; throttling progress
    progress.py         baca byte upload + meta task Celery → satu tampilan progress
    thumbnails.py       murni: make_thumbnails(src, sizes, out_dir) -> dict[size, path]
  worker/
    celery_app.py       app Celery (broker sqla, backend db). TIDAK BOLEH meng-import app FastAPI
    tasks.py            generate_thumbnails(image_id): wrapper tipis di atas services.thumbnails
tests/
  conftest.py           folder data temp, DB test, TestClient, Celery eager, fixture gambar dibuat dengan Pillow
  test_validation.py  test_thumbnails.py  test_multipart_stream.py
  test_sanitize.py    test_cleanup.py     test_security.py
  test_uploads_api.py test_images_api.py
.env.example            contoh konfigurasi (API_KEYS=dev-key-ganti-saya); .env asli masuk .gitignore
```

Aturan dependensi: `api → services → models/storage`; `worker.tasks → services`. Tidak ada yang meng-import `api` kecuali `main`.

## 5. Model data

**Upload**
| kolom | catatan |
|---|---|
| id | UUID str, PK |
| status | `created → uploading → completed`, atau `failed` (client terputus / error fatal) |
| bytes_expected | dari `Content-Length` |
| bytes_received | penulisan di-throttle |
| rejected_files | list JSON `[{filename, reason, code}]` |
| expires_at | `created_at + 10 menit`. Setelah lewat, request file ditolak dengan `410` |
| created_at, updated_at | |

**Image**
| kolom | catatan |
|---|---|
| id | UUID str, PK |
| upload_id | FK → Upload |
| original_filename | sudah disanitasi, hanya untuk ditampilkan |
| stored_filename | `<uuid>.<ext>`, tidak pernah diambil dari input user |
| content_type | **dideteksi** oleh Pillow, bukan dari header client |
| size_bytes, width, height | ukuran **setelah** metadata dihapus |
| title, description | nullable, diubah lewat PATCH |
| status | `queued → processing → done`, atau `failed` |
| error | nullable |
| task_id | id Celery (internal, tidak diekspos) |
| thumbnails | JSON `{ "128": "…webp", … }` |
| created_at, updated_at | |

**Sumber kebenaran:** kolom `status` di DB adalah catatan permanen. Meta task Celery hanya dibaca untuk `current/total` saat gambar sedang `processing`, karena hasil Celery bisa kedaluwarsa.

## 6. API

Semua endpoint di bawah wajib membawa header `X-API-Key`. Tanpa key atau key salah → `401`. Endpoint tanpa auth: `GET /health`, `/docs`, `/openapi.json`.

| Method | Path | Hasil |
|---|---|---|
| GET | `/health` | `200 {status: "ok"}` (tanpa auth) |
| POST | `/uploads` | `201 {upload_id, status, expires_at}` |
| POST | `/uploads/{id}/files` | `202 {upload_id, accepted:[{image_id, filename}], rejected:[{filename, reason, code}]}` |
| GET | `/uploads/{id}` | `200` % upload + `status` dan `progress {current,total,percent}` tiap gambar + daftar yang ditolak |
| GET | `/images` | list berhalaman (`skip`, `limit≤100`, opsional `upload_id`, `status`) |
| GET | `/images/{id}` | metadata + URL thumbnail |
| GET | `/images/{id}/file` | byte file asli (content-type hasil deteksi) |
| GET | `/images/{id}/thumbnails/{size}` | `size ∈ {128,256,512}`, 404 jika belum siap |
| PATCH | `/images/{id}` | ubah `title` / `description` (Pydantic: title ≤ 100, description ≤ 1000) |
| DELETE | `/images/{id}` | `204`. Menghapus baris, file asli, dan thumbnail |

Error: `400` body bukan multipart/form-data yang valid (Content-Type tanpa boundary, format rusak, atau body berakhir sebelum boundary penutup) · `401` API key tidak ada / salah · `404` id tidak dikenal · `409` sesi sudah dipakai / sedang berjalan · `410` sesi kedaluwarsa · `411` tidak ada Content-Length · `413` body request lebih besar dari `3 × 1 MB + overhead` (ditolak sebelum dibaca) · `422` parameter tidak valid. Kegagalan per file masuk ke `rejected[]` dengan kode `too_large`, `unsupported_type`, `invalid_image`, `too_many_files`, `empty_file`.

## 7. Validasi (per bagian file)

| Langkah | Kapan | Menolak |
|---|---|---|
| Bagian berupa file (punya filename) | header | field form diabaikan |
| Jumlah file ≤ 3 | awal bagian | bagian ke-4 dst. → `too_many_files`; byte-nya dibuang, tidak ditulis |
| Allowlist ekstensi `.jpg .jpeg .png .gif .webp` | header | `unsupported_type` |
| Allowlist Content-Type | header | `unsupported_type` |
| Magic bytes | ≥12 byte pertama | `unsupported_type` (berhenti menulis) |
| Ukuran ≤ 1 MB | setiap chunk | `too_large` (berhenti menulis, hapus temp, lanjut parsing bagian berikutnya) |
| Tidak kosong | akhir bagian | `empty_file` |
| Pillow `open` + `verify`, lalu buka ulang untuk ukuran; batas `MAX_IMAGE_PIXELS`; format harus sesuai tipe ekstensi | akhir bagian (threadpool) | `invalid_image` |
| **Hapus metadata** (EXIF, GPS, komentar, chunk teks PNG); orientasi EXIF diterapkan dulu ke piksel agar gambar tidak miring | setelah lolos verify, sebelum atomic move (threadpool) | `invalid_image` jika gagal disimpan ulang |
| Nama file | selalu | disanitasi untuk tampilan; nama simpanan berupa UUID |

Cara menghapus metadata per format (di `services/sanitize.py`):

| Format | Cara | Kualitas |
|---|---|---|
| JPEG | `exif_transpose`, lalu simpan ulang dengan `quality="keep"` tanpa `exif` | tabel kuantisasi asli dipakai ulang, penurunan kualitas minimal (kecuali jika gambar harus diputar) |
| PNG | simpan ulang tanpa `pnginfo` / chunk `eXIf` | lossless |
| GIF | simpan ulang dengan `save_all=True` (semua frame, durasi, loop), tanpa komentar | lossless |
| WebP | simpan ulang dengan `save_all=True`; `lossless=True` jika sumbernya lossless, selain itu `quality=90`; tanpa `exif`/`xmp` | lossless atau mendekati |

Profil warna ICC **dipertahankan** (penting untuk akurasi warna dan tidak berisi data pribadi).

## 8. Fase (setiap fase diakhiri dengan test yang lolos)

### Fase 0: Setup dan spike risiko
- `uv add` dependensi runtime, `uv add --dev pytest httpx`. Biarkan `.python-version` di 3.14.
- Buat struktur folder, `config.py`, tambahkan `data/` dan `.env` ke `.gitignore`.
- `config.py` memuat `API_KEYS` (list, dibaca dari env / `.env`) dan `UPLOAD_SESSION_TTL_SECONDS=600`. Jika `API_KEYS` kosong, aplikasi **menolak untuk start** (fail closed). Buat `.env.example`.
- **Spike (kerjakan pertama, ini risiko terbesar):** app Celery minimal dengan `broker_url="sqla+sqlite:///data/celery-broker.db"` dan `result_backend="db+sqlite:///data/celery-results.db"`. Jalankan `celery -A app.worker.celery_app worker --pool=solo -l info` di Windows. Pastikan satu task berjalan dan `update_state(PROGRESS, meta)` bisa dibaca kembali dengan `AsyncResult(id).info` dari proses lain.
  - Jika transport SQLAlchemy kombu gagal dengan SQLAlchemy 2.1, pin `sqlalchemy<2.1` dan uji ulang sebelum lanjut.
- ✅ Selesai jika: progress task spike terlihat dari proses Python terpisah.

### Fase 1: DB dan model
- `db.py`: engine dengan event `connect` yang menjalankan `PRAGMA journal_mode=WAL` dan `PRAGMA busy_timeout=5000`; `check_same_thread=False`; dependency `get_db`.
- `models.py`: Upload (termasuk `expires_at`, dengan index), Image. `Base.metadata.create_all` saat startup (tanpa Alembic untuk cakupan ini).
- Semua waktu disimpan dalam **UTC** (timezone-aware) agar perbandingan kedaluwarsa konsisten.
- ✅ Test: buat/baca baris di DB temp; WAL aktif.

### Fase 2: Schema
- `UploadCreated`, `FileResult`, `RejectedFile`, `UploadFilesResponse`, `ProgressInfo`, `UploadStatusResponse`, `ImageRead`, `ImageUpdate` (`model_config = ConfigDict(from_attributes=True)`, `extra="forbid"` pada input), `Page[ImageRead]`.
- Tambahkan contoh `json_schema_extra` agar Swagger menampilkan payload yang realistis.

### Fase 3: Service murni (TDD, tanpa HTTP)
- `validation.py`: `check_extension`, `check_content_type`, `sniff_magic(head: bytes) -> str|None`, `verify_image(path) -> (format, width, height)`.
- `storage.py`: `new_temp_path()`, `promote(temp, ext) -> stored_name` (atomic `os.replace`), `delete_image_files(image)`.
- `thumbnails.py`: `make_thumbnails(src, sizes, out_dir, on_progress=None)`. Memakai `ImageOps.exif_transpose`, `thumbnail()` (menjaga rasio aspek), frame pertama GIF animasi, simpan sebagai **WebP** (mendukung transparansi), menimpa file yang ada (idempoten).
- `sanitize.py`: `strip_metadata(path, format) -> (width, height, size_bytes)` sesuai tabel di §7. Menulis ke file temp kedua lalu `os.replace`, sehingga file asli tidak rusak jika penyimpanan gagal.
- `cleanup.py`: `cleanup_abandoned(db, now)`:
  1. Hapus baris Upload yang `expires_at < now` **dan tidak punya gambar** (status apa pun: sesi kosong, gagal, atau semua file-nya ditolak).
  2. Upload yang `expires_at < now` dan masih `uploading` (proses mati di tengah upload) → tandai `failed`. Gambar yang sudah diterima tetap dipertahankan.
  3. Hapus file di folder temp yang lebih tua dari masa berlaku sesi (sisa upload yang terputus/crash).
  4. Kembalikan ringkasan `{sessions_deleted, sessions_failed, temp_files_deleted}` untuk di-log.
- ✅ Test dengan fixture yang dibuat Pillow: PNG/JPEG/GIF/WebP valid, PNG RGBA, JPEG dengan rotasi EXIF, file teks yang di-rename menjadi `.png`, PNG terpotong, file kosong, file 1 MB + 1 byte, header pixel-bomb.
- ✅ Test sanitize: JPEG dengan EXIF berisi GPS → setelah diproses tidak ada EXIF/GPS, piksel sudah diputar sesuai orientasi, ukuran sama; PNG dengan chunk teks → chunk hilang; GIF animasi → jumlah frame sama; profil ICC tetap ada.
- ✅ Test cleanup (dengan `now` yang di-inject): sesi kedaluwarsa tanpa gambar terhapus; sesi kedaluwarsa dengan gambar tetap ada; sesi belum kedaluwarsa tidak tersentuh; `uploading` basi → `failed`; file temp lama terhapus, file temp baru tidak.

### Fase 4: Handler multipart streaming
- `MultipartStreamHandler` membungkus `python_multipart.MultipartParser` (boundary diambil dari Content-Type request). Callback hanya memperbarui state di memori dan mengantrekan pekerjaan. **Endpoint async** yang memasukkan `chunk` dan melakukan I/O (penulisan file temp via `anyio.to_thread.run_sync`), sehingga parser tetap sync dan bisa di-unit-test.
- State per bagian: `index, filename, declared_type, bytes, temp_path, rejected_reason`. Menegakkan jumlah file, batas ukuran, dan cek magic bytes seperti di §7.
- Setelah setiap chunk: `received += len(chunk)`. Tulis `bytes_received` ke DB hanya jika sudah bertambah ≥ 64 KB atau ≥ 5 % sejak penulisan terakhir, ditambah satu penulisan akhir.
- `try/finally`: saat `ClientDisconnect` atau exception apa pun, hapus semua file temp dari request tersebut dan tandai Upload sebagai `failed`.
- ✅ Test: masukkan body multipart buatan tangan dengan ukuran chunk acak (1 byte, 7 byte, seluruh body). Cek 4 file → 3 diproses + 1 `too_many_files`; file tengah yang terlalu besar ditolak sementara file 1 dan 3 berhasil; boundary terpotong di antara chunk.

### Fase 5: Auth, startup, dan endpoint upload
- `security.py`: `APIKeyHeader(name="X-API-Key", auto_error=False)` + dependency `require_api_key` yang membandingkan dengan setiap key di `API_KEYS` memakai `secrets.compare_digest` (tahan timing attack). Gagal → `401` dengan header `WWW-Authenticate: ApiKey`. Key **tidak pernah** di-log.
- Pasang `dependencies=[Depends(require_api_key)]` saat `include_router` untuk router `uploads` dan `images`, sehingga tidak ada endpoint yang terlewat. `GET /health` berada di luar router tersebut. Swagger otomatis menampilkan tombol **Authorize**.
- `main.py` lifespan: buat folder → `create_all` → `cleanup_abandoned(now=utcnow())` → log ringkasannya.
- `POST /uploads` (mengisi `expires_at`).
- `POST /uploads/{id}/files`: menerima `request: Request` agar FastAPI tidak mem-buffer body. Pengecekan: sesi ada (404), belum kedaluwarsa (410), status `created` (409), `Content-Length` ada (411) dan ≤ batas (413). Kedaluwarsa hanya dicek saat request dimulai; upload yang sudah berjalan tidak diputus.
- Untuk setiap bagian yang lolos verify: `strip_metadata` (di threadpool), lalu lanjut ke langkah di bawah. Set status `uploading`, jalankan handler. Untuk setiap bagian yang diterima: promote file → insert Image (`queued`) → **commit** → `generate_thumbnails.delay(image_id)` → simpan `task_id`. Set status `completed`.
- **Swagger:** karena endpoint membaca raw stream, FastAPI tidak membuat file picker sendiri. Tambahkan `openapi_extra={"requestBody": {"content": {"multipart/form-data": {"schema": {"type":"object","properties":{"files":{"type":"array","items":{"type":"string","format":"binary"},"maxItems":3}}}}}, "required": True}}`.
- `GET /uploads/{id}`: `services/progress.py` menggabungkan persentase upload dengan status tiap gambar. Untuk gambar `processing` dibaca `AsyncResult(task_id).info` → `{current, total}`; untuk `done`/`failed` cukup dari DB.
- ✅ Test (TestClient, Celery eager): happy path 3 file; campuran valid/tidak valid; 4 file; sesi dipakai ulang → 409; sesi tidak ada → 404; sesi kedaluwarsa → 410; request terlalu besar → 413; body multipart rusak / tanpa boundary → 400; file asli yang tersimpan tidak mengandung EXIF/GPS.
- ✅ Test auth: tanpa header → 401; key salah → 401; key benar → lolos; `/health` dan `/docs` tanpa key → 200; app gagal start jika `API_KEYS` kosong. Test otomatis memeriksa **setiap** route di `app.routes` (kecuali daftar publik) mengembalikan 401 tanpa key, agar route baru tidak lupa diberi auth.

### Fase 6: Worker Celery
- `celery_app.py`: broker dan backend SQLite (file DB terpisah dari `app.db`), `task_track_started=True`, `result_extended=True`, `result_expires=1 hari`, `worker_prefetch_multiplier=1`, `task_acks_late=True`.
- `tasks.generate_thumbnails(self, image_id)` dengan `bind=True`:
  1. Muat Image. Jika tidak ada atau file-nya hilang (sudah dihapus) → log lalu return.
  2. Set `status=processing`, commit.
  3. `make_thumbnails(..., on_progress=lambda i, n: self.update_state(state="PROGRESS", meta={"current": i, "total": n}))`.
  4. Muat ulang Image (bisa saja dihapus saat langkah 3; jika begitu, hapus thumbnail baru lalu return). Simpan `thumbnails`, `status=done`, commit.
  5. Saat exception: `status=failed`, `error` berisi pesan tetap yang aman untuk client (bukan `str(e)`, karena pesan Pillow memuat path file di server), commit, raise ulang agar detailnya tercatat di log worker. `autoretry_for=(OperationalError,)` dengan backoff khusus untuk error lock SQLite.
- Hanya session DB yang singkat. Tidak ada session yang tetap terbuka selama Pillow bekerja.
- ✅ Test: panggil fungsi task langsung (eager) → thumbnail ada, status `done`; gambar dihapus sebelum task jalan → tidak crash; file rusak → `failed`.

### Fase 7: CRUD gambar
- List (berhalaman, filter), get, download file (`FileResponse` dengan content-type hasil deteksi), download thumbnail (404 sampai siap), PATCH metadata, DELETE (baris + file asli + thumbnail; file tidak ditemukan bukan error).
- ✅ Test untuk setiap endpoint, termasuk 404 dan validasi PATCH (`extra="forbid"`, batas panjang).

### Fase 8: Penyempurnaan
- Exception handler global dengan satu bentuk error yang konsisten `{detail, code}`.
- `responses={...}` pada route agar Swagger mencantumkan 400/401/404/409/410/411/413. Tag: `uploads`, `images`, `health`.
- `README.md`: setup (`uv sync`, salin `.env.example` ke `.env` dan isi `API_KEYS`), menjalankan API (`uv run fastapi dev main.py`), menjalankan worker (`uv run celery -A app.worker.celery_app worker --pool=solo -l info`), panduan dengan curl (dengan header `X-API-Key`) dan Swagger (tombol Authorize), catatan desain, keterbatasan, dan bagian **"Keamanan untuk produksi"** (rate limiting, CORS, dan celah lain yang belum diimplementasikan; lihat ADR-19).
- ✅ Selesai jika: seluruh `pytest` hijau dan uji manual lewat Swagger berhasil dengan worker sungguhan berjalan.

## 9. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Transport SQLAlchemy kombu berstatus "experimental"; belum teruji dengan SA 2.1 | Spike Fase 0 sebelum yang lain; fallback pin `sqlalchemy<2.1` |
| SQLite `database is locked` (API + worker + penulisan progress + polling broker) | WAL, busy_timeout, broker/results di file terpisah, penulisan progress di-throttle, transaksi singkat, retry pada `OperationalError` |
| Celery di Windows | `--pool=solo` (satu task dalam satu waktu, cukup untuk cakupan ini) |
| Memblokir event loop di endpoint streaming | Semua pekerjaan disk, Pillow, dan DB lewat `anyio.to_thread` / threadpool |
| File yatim (orphan) setelah crash | temp → atomic move; pembersihan di `finally`; DELETE mengabaikan file yang hilang |
| Swagger tidak bisa menampilkan progress bar | Polling `GET /uploads/{id}` dari Swagger menampilkan angka yang berubah; UI di luar cakupan |
| Menghapus metadata menurunkan kualitas gambar | JPEG memakai `quality="keep"`; PNG/GIF lossless; WebP lossless jika sumbernya lossless |
| API key bocor lewat log atau repo | Key tidak pernah di-log; `.env` masuk `.gitignore`; hanya `.env.example` dengan nilai contoh yang di-commit |
| Cleanup startup menghapus upload yang sedang berjalan di proses lain | Hanya menyentuh sesi yang sudah lewat 10 menit dan file temp yang lebih tua dari 10 menit; upload maks. ~3 MB selesai jauh sebelum itu |

## 10. Di luar cakupan (bisa ditambahkan nanti)

Kepemilikan gambar per API key, penyimpanan S3, migrasi Alembic, push SSE/WebSocket, halaman HTML demo, Docker, mengganti file gambar (`PUT`).

**Didokumentasikan sebagai pekerjaan produksi** (lihat ADR-19 di ARCHITECTURE.md): rate limiting, CORS, header download (`nosniff`, `Content-Disposition`), batas waktu upload lambat, batas jumlah frame GIF, batas jumlah bagian multipart, penghitungan total byte sebagai cadangan `Content-Length`, normalisasi nama file, dan cleanup terjadwal (saat ini hanya saat startup).
