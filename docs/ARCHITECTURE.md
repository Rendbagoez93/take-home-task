# Arsitektur: Image Upload Service

Dokumen ini mencatat keputusan arsitektur proyek dan alasannya. Rencana langkah demi langkah ada di [PLAN.md](PLAN.md).

Setiap keputusan ditulis dengan format: **Keputusan**, **Alternatif yang dipertimbangkan**, **Alasan**, **Konsekuensi**.

---

## 1. Konteks

Kebutuhan:
- Endpoint untuk meng-upload gambar (hingga 3 file per request, maks. 1 MB per file).
- Progress yang bisa dipantau: progress upload (byte) dan progress pemrosesan.
- CRUD sederhana untuk gambar.
- Validasi file upload.
- Lapisan keamanan: auth API key, penghapusan metadata pribadi dari file asli, dan pembersihan sesi terbengkalai.

Stack wajib: **FastAPI, Swagger, Pydantic, Celery**.

Batasan lingkungan:
- Development di **Windows 11**, **tanpa Docker**.
- **Python 3.14**.
- Tidak ada Redis/RabbitMQ; semua penyimpanan memakai **SQLite**.

---

## 2. Gambaran umum

```
┌────────┐ 1. POST /uploads              ┌─────────────────────────────┐
│        │ ────────────────────────────► │                             │
│        │ 2. POST /uploads/{id}/files   │  FastAPI (async)            │
│ Client │ ────── raw stream ──────────► │  - parsing multipart stream │──► data/uploads/ (disk)
│        │                               │  - validasi per file        │
│        │ 3. GET /uploads/{id} (poll)   │  - hitung byte → app.db     │──► data/app.db
│        │ ────────────────────────────► │  - enqueue task per gambar  │
└────────┘                               └──────────────┬──────────────┘
                                                        │ task.delay(image_id)
                                                        ▼
                                          data/celery-broker.db (antrean)
                                                        │
                                                        ▼
                                         ┌─────────────────────────────┐
                                         │ Worker Celery (--pool=solo) │──► thumbnail di disk
                                         │ - 3 thumbnail per gambar    │──► status di app.db
                                         │ - update_state(PROGRESS)    │──► data/celery-results.db
                                         └─────────────────────────────┘
```

Tiga proses berjalan: **API (uvicorn)**, **worker Celery**, dan **client**. API dan worker berbagi folder `data/` (disk dan file SQLite).

Setiap request dari client membawa header `X-API-Key` (kecuali `/health`, `/docs`, `/openapi.json`). Worker Celery tidak menerima request dari luar, jadi tidak memerlukan API key.

---

## 3. Keputusan arsitektur

### ADR-01: Upload dua langkah (sesi upload)

**Keputusan:** client membuat sesi dulu dengan `POST /uploads` (mendapat `upload_id`), lalu mengirim file ke `POST /uploads/{upload_id}/files`.

**Alternatif:**
- Satu request; server membuat ID dan mengembalikannya di akhir.
- Client membuat UUID sendiri dan mengirimnya lewat header `X-Upload-ID`.

**Alasan:**
- Client hanya bisa melakukan polling progress upload jika sudah tahu ID-nya **sebelum** upload selesai. Dengan satu request, ID baru diterima setelah upload selesai, sehingga progress upload tidak bisa dipantau.
- Server tetap memegang kendali atas pembuatan ID, sehingga tidak perlu memvalidasi ID dari client atau menangani ID ganda.
- Sesi menjadi "induk" alami untuk 1–3 gambar dalam satu batch (relasi Upload 1—N Image).

**Konsekuensi:**
- Ada satu request tambahan (biayanya kecil).
- Satu sesi hanya menerima **satu** request file. Request kedua → `409`, karena dua request akan mencampur hitungan byte. Untuk upload lagi, buka sesi baru.
- Sesi yang dibuat tapi tidak pernah dipakai bisa menumpuk. Karena itu sesi punya masa berlaku 10 menit dan dibersihkan saat startup (lihat ADR-18).

---

### ADR-02: Parsing raw stream, bukan `UploadFile`

**Keputusan:** endpoint upload membaca `request.stream()` dan mem-parsing multipart secara bertahap dengan `MultipartParser` level rendah dari `python-multipart`.

**Alternatif:**
- `UploadFile` bawaan FastAPI.
- Library `streaming-form-data`.

**Alasan:**
- Dengan `UploadFile`, Starlette sudah membaca **seluruh** body sebelum kode endpoint berjalan. Tidak ada lagi yang bisa diukur untuk progress upload, dan batas ukuran baru bisa dicek setelah semua data diterima.
- Streaming memungkinkan batas 1 MB per file dan penolakan file ke-4 **selama** upload berlangsung, tanpa menulis byte yang tidak perlu ke disk.
- `streaming-form-data` tidak punya wheel Python 3.14 untuk Windows (butuh compiler C). `python-multipart` murni Python dan sudah menjadi dependensi FastAPI.

**Konsekuensi:**
- Lebih banyak kode: parser multipart dibungkus dalam class `MultipartStreamHandler`. Class ini dibuat **sync dan tanpa I/O** agar bisa di-unit-test dengan body buatan dan ukuran chunk acak.
- Swagger tidak otomatis menampilkan file picker untuk endpoint ini. Skema request body ditambahkan manual lewat `openapi_extra`.
- Request wajib memiliki `Content-Length` (sebagai total untuk persentase progress). Tanpa itu → `411`.

---

### ADR-03: Dua jenis progress, dikirim lewat polling ke satu endpoint

**Keputusan:**
- **Progress upload:** API menghitung byte yang diterima dan menyimpannya di kolom `Upload.bytes_received`.
- **Progress pemrosesan:** worker Celery memanggil `update_state(state="PROGRESS", meta={"current", "total"})` setiap selesai membuat satu thumbnail.
- Keduanya digabung dan dikirim oleh **satu** endpoint: `GET /uploads/{id}`. Client melakukan polling.

**Alternatif:** Server-Sent Events atau WebSocket; mengekspos `task_id` Celery ke client agar client mem-polling `/tasks/{task_id}`.

**Alasan:**
- Polling paling sederhana, mudah di-test, dan bisa dicoba langsung dari Swagger. SSE/WebSocket tidak bisa ditampilkan Swagger.
- Satu endpoint berarti client tidak perlu tahu ada Celery di belakangnya. Celery tetap menjadi detail internal yang bisa diganti tanpa mengubah kontrak API.
- Progress upload disimpan di DB, bukan di `dict` memori, karena memori tidak terlihat oleh proses lain (worker uvicorn lain atau worker Celery).

**Konsekuensi:**
- Penulisan `bytes_received` **di-throttle** (tiap ≥ 64 KB atau ≥ 5 %, plus satu penulisan akhir) agar SQLite tidak dibanjiri penulisan.
- Progress upload dihitung untuk **seluruh request** (berdasarkan `Content-Length`, termasuk boundary multipart), bukan per file.

---

### ADR-04: Celery hanya untuk pemrosesan; satu task per gambar

**Keputusan:** API menyimpan file ke disk, lalu mengirim **hanya `image_id`** ke Celery. Satu task per gambar membuat 3 thumbnail.

**Alternatif:** satu task per batch; mengirim isi file ke task.

**Alasan:**
- Worker Celery adalah proses terpisah; stream file tidak bisa dikirim ke sana. Mengirim byte gambar lewat broker SQLite juga boros. Cukup ID-nya, karena path file bisa dibaca dari DB.
- Satu task per gambar memberi progress, retry, dan status gagal yang **independen**. Satu gambar rusak tidak menggagalkan gambar lain.

**Konsekuensi:**
- API dan worker harus bisa membaca folder `data/` yang sama. Di satu mesin lokal hal ini otomatis.
- Task harus **idempoten** (retry hanya menimpa thumbnail) dan tahan terhadap gambar yang sudah dihapus (lihat ADR-09).

---

### ADR-05: Broker dan result backend Celery memakai SQLite

**Keputusan:** `broker_url = "sqla+sqlite:///data/celery-broker.db"` (transport SQLAlchemy dari kombu) dan `result_backend = "db+sqlite:///data/celery-results.db"`.

**Alternatif:** Redis di WSL; Memurai (Redis untuk Windows); RabbitMQ.

**Alasan:**
- Tanpa Docker, Redis tidak punya build native Windows. SQLite tidak butuh server tambahan sama sekali, jadi proyek bisa dijalankan hanya dengan `uv sync`.
- Cocok untuk skala take-home: volume task kecil.

**Konsekuensi:**
- Transport SQLAlchemy kombu berstatus **experimental** dan bekerja dengan polling (ada sedikit jeda sebelum task diambil). Kompatibilitasnya dengan SQLAlchemy 2.1 diverifikasi lewat **spike di Fase 0** sebelum kode lain ditulis. Fallback: pin `sqlalchemy<2.1`.
- Broker dan results memakai **file terpisah** dari `app.db` untuk mengurangi perebutan lock.
- Tidak cocok untuk produksi. Untuk produksi, ganti ke Redis/RabbitMQ. Ini hanya mengubah konfigurasi, bukan kode task.

---

### ADR-06: SQLite sebagai DB aplikasi, dengan pengaturan untuk akses bersamaan

**Keputusan:** SQLite melalui SQLAlchemy 2.x, dengan `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, dan `check_same_thread=False`. Tabel dibuat dengan `create_all` saat startup.

**Alternatif:** PostgreSQL; Alembic untuk migrasi.

**Alasan:**
- Tidak butuh server, cukup untuk cakupan ini, dan mudah dijalankan reviewer.
- API dan worker sama-sama menulis ke `app.db`. Tanpa WAL dan busy timeout, error `database is locked` akan sering muncul. WAL mengizinkan pembaca berjalan bersamaan dengan satu penulis.

**Konsekuensi:**
- Transaksi harus **singkat**: tidak boleh ada session DB yang terbuka selama pemrosesan gambar atau I/O jaringan.
- Worker punya engine dan session sendiri. Task melakukan retry otomatis hanya untuk `OperationalError` (lock).
- Tanpa Alembic, perubahan skema berarti menghapus DB lokal. Bisa diterima untuk take-home.

---

### ADR-07: DB adalah sumber kebenaran status, bukan Celery

**Keputusan:** kolom `Image.status` (`queued → processing → done | failed`) adalah catatan permanen. Meta Celery hanya dibaca untuk `current/total` selama gambar berstatus `processing`.

**Alternatif:** status sepenuhnya dari `AsyncResult` Celery.

**Alasan:**
- Hasil Celery **kedaluwarsa** (`result_expires`, default 1 hari). Setelah itu status akan hilang.
- Query list dan filter (`GET /images?status=done`) butuh status di DB.

**Konsekuensi:** worker wajib memperbarui DB di setiap transisi status, termasuk saat gagal (`status=failed`, `error=...`).

Alur status:

```
Upload:  created ──► uploading ──► completed
                        └────────► failed      (client terputus / error fatal)

Image:   queued ──► processing ──► done
                        └────────► failed      (gambar rusak / error saat pemrosesan)
```

---

### ADR-08: Aturan multi-file: sukses parsial, maks. 3 file per request, 1 MB per file

**Keputusan:**
- Setiap file dinilai sendiri. File valid diterima; file tidak valid masuk ke daftar `rejected` beserta alasannya.
- File ke-4 dst. dalam request yang sama ditolak (`too_many_files`) saat streaming, dan byte-nya dibuang.
- File di atas 1 MB ditolak (`too_large`) begitu batasnya terlewati. Parsing tetap lanjut ke file berikutnya.
- Request yang `Content-Length`-nya melebihi `3 × 1 MB + overhead` langsung ditolak dengan `413` sebelum body dibaca.

**Alternatif:** all-or-nothing (satu file gagal → semua ditolak).

**Alasan:** sukses parsial lebih realistis dan lebih ramah pengguna. Satu file rusak tidak memaksa pengguna meng-upload ulang file lain yang sudah valid.

**Konsekuensi:**
- Response `202` selalu berisi dua daftar: `accepted` dan `rejected`.
- File yang ditolak **tidak** dibuat sebagai baris `Image` (agar CRUD hanya berisi gambar sungguhan). File tersebut dicatat di kolom JSON `Upload.rejected_files` agar tetap terlihat saat polling.

---

### ADR-09: Urutan operasi untuk menjaga konsistensi DB, disk, dan Celery

**Keputusan:**
1. Tulis ke **file temp** → validasi → `os.replace` (atomic) ke lokasi final.
2. **Commit** baris `Image` **sebelum** `task.delay()`.
3. Pembersihan di `try/finally`: jika client terputus atau terjadi error, semua file temp dari request itu dihapus dan Upload ditandai `failed`.
4. Worker mengecek ulang keberadaan gambar sebelum dan sesudah membuat thumbnail. Jika gambar sudah dihapus, worker membersihkan thumbnail barunya lalu keluar tanpa error.
5. DELETE menghapus baris, file asli, dan semua thumbnail. File yang sudah tidak ada bukan error.

**Alasan:** DB, disk, dan antrean Celery tidak bisa berada dalam satu transaksi. Urutan ini mencegah tiga masalah:
- File setengah jadi di lokasi final.
- Worker yang mencari baris yang belum di-commit (worker bisa lebih cepat dari commit).
- File yatim setelah crash atau penghapusan saat pemrosesan berlangsung.

**Konsekuensi:** jika proses mati tepat di antara commit dan `delay()`, gambar bisa tertinggal di `queued`. Ini risiko kecil yang diterima untuk cakupan ini (solusi produksi: pola outbox atau job pemindai ulang).

---

### ADR-10: Validasi berlapis, dan content-type dideteksi sendiri

**Keputusan:** validasi berurutan dari yang termurah ke yang terkuat: ada filename → jumlah file → ekstensi → Content-Type → magic bytes → ukuran → tidak kosong → Pillow `verify()` dengan batas `MAX_IMAGE_PIXELS`. `content_type` yang disimpan adalah hasil deteksi Pillow, bukan header dari client.

**Alternatif:** hanya mengecek ekstensi dan Content-Type.

**Alasan:**
- Ekstensi dan Content-Type dikirim client, jadi mudah dipalsukan. Hanya decoding sungguhan (Pillow) yang membuktikan file benar-benar gambar.
- Pengecekan murah dilakukan lebih dulu agar file yang jelas salah ditolak tanpa biaya decoding.
- `MAX_IMAGE_PIXELS` mencegah *decompression bomb* (file kecil yang mengembang menjadi gambar raksasa di memori).
- Nama file dari user tidak pernah dipakai sebagai path (mencegah path traversal seperti `../../etc/passwd`). File disimpan dengan nama UUID.

**Konsekuensi:** Pydantic memvalidasi data terstruktur (parameter, metadata, response). Validasi isi file adalah kode sendiri di `services/validation.py`, karena Pydantic tidak bisa memeriksa isi biner.

---

### ADR-11: Thumbnail: 3 ukuran, format WebP

**Keputusan:** ukuran sisi terpanjang 128, 256, 512 px (bisa dikonfigurasi), memakai `Image.thumbnail()`, `ImageOps.exif_transpose`, frame pertama untuk GIF animasi, disimpan sebagai **WebP**.

**Alasan:**
- `thumbnail()` menjaga rasio aspek.
- `exif_transpose` mencegah foto dari ponsel tampil miring.
- WebP mendukung transparansi (gambar RGBA tidak bisa disimpan sebagai JPEG) dan ukurannya kecil.
- Melakukan encoding ulang juga membuang payload berbahaya yang mungkin tersembunyi di file asli.

**Konsekuensi:** progress pemrosesan bernilai `1/3, 2/3, 3/3`. Logika thumbnail dibuat sebagai **fungsi murni** sehingga bisa di-test tanpa Celery.

---

### ADR-12: Batas async/sync

**Keputusan:** endpoint upload bersifat `async` (wajib, karena membaca stream). Semua pekerjaan blocking di dalamnya (tulis disk, Pillow, query DB) dijalankan lewat `anyio.to_thread.run_sync`. Endpoint CRUD lainnya memakai `def` biasa dengan SQLAlchemy sync, yang otomatis dijalankan FastAPI di threadpool.

**Alternatif:** SQLAlchemy async (`aiosqlite`) di seluruh aplikasi.

**Alasan:**
- Kode blocking di dalam fungsi `async` menghentikan **semua** request lain di server.
- SQLAlchemy sync lebih sederhana dan bisa dipakai bersama oleh API dan worker Celery (worker Celery bersifat sync).

**Konsekuensi:** setiap endpoint harus dipilih dengan sadar: `async` hanya untuk endpoint streaming, `def` untuk yang lain.

---

### ADR-13: Struktur kode berlapis

**Keputusan:**

```
api/       → hanya HTTP (route, status code), tanpa logika bisnis
services/  → logika: validasi, penyimpanan, streaming, progress, thumbnail
models/    → tabel SQLAlchemy
schemas/   → model Pydantic
worker/    → app Celery + task (wrapper tipis yang memanggil services)
config.py  → pydantic-settings, dipakai bersama oleh API dan worker
```

Aturan dependensi: `api → services → models/storage`; `worker → services`. App Celery **tidak boleh** meng-import app FastAPI.

**Alasan:**
- Logika bisa di-test tanpa HTTP atau Celery.
- Mencegah circular import antara FastAPI dan Celery.
- Penyimpanan berada di balik antarmuka kecil (`storage.py`), sehingga bisa diganti ke S3 nanti tanpa mengubah route atau task.

**Konsekuensi:** ada beberapa file tambahan dibanding menaruh semuanya di `main.py`, tetapi setiap file punya satu tanggung jawab yang jelas.

---

### ADR-14: Lingkungan: Windows, Python 3.14, tanpa Docker

**Keputusan:** worker dijalankan dengan `--pool=solo`; Python tetap 3.14; dependensi dikelola dengan `uv`.

**Alasan:**
- Pool default Celery (prefork) tidak berjalan di Windows. `solo` menjalankan satu task dalam satu waktu, cukup untuk cakupan ini.
- Semua dependensi sudah dicek punya prebuilt wheel untuk Python 3.14 di Windows: celery 5.6, kombu 5.7, pillow 13, sqlalchemy 2.1, fastapi 0.162, python-multipart 0.0.33.

**Konsekuensi:**
- Pemrosesan berjalan berurutan (tidak paralel). Untuk paralelisme di Windows bisa memakai `--pool=threads`, atau Linux/Docker di produksi.
- Jika muncul masalah kompatibilitas 3.14, fallback ke Python 3.13.

---

### ADR-15: Strategi testing

**Keputusan:**
- **Unit test** untuk fungsi murni: validasi, thumbnail, `MultipartStreamHandler` (dengan ukuran chunk acak, termasuk boundary yang terpotong di antara chunk).
- **Test API** dengan `TestClient` FastAPI dan Celery dalam mode **eager** (`task_always_eager=True`).
- Fixture gambar dibuat oleh Pillow saat test berjalan (tidak ada file biner di repo): gambar valid, PNG RGBA, JPEG dengan rotasi EXIF, file teks yang di-rename `.png`, PNG terpotong, file kosong, file 1 MB + 1 byte, header pixel-bomb.
- Folder data dan DB memakai folder temp per test.

**Alasan:** bagian paling berisiko (parsing stream dan validasi) bisa di-test secara deterministik tanpa server atau worker sungguhan.

**Konsekuensi:** mode eager tidak menguji broker SQLite yang sebenarnya. Hal itu ditutup oleh spike Fase 0 dan uji manual dengan worker sungguhan di Fase 8.

---

### ADR-16: Auth dengan API key

**Keputusan:**
- Semua endpoint di router `uploads` dan `images` wajib membawa header `X-API-Key`. Endpoint publik hanya `GET /health`, `/docs`, dan `/openapi.json`.
- Daftar key dibaca dari setting `API_KEYS` (env / `.env`). Jika kosong, aplikasi **menolak untuk start** (fail closed).
- Pengecekan memakai `fastapi.security.APIKeyHeader` dan `secrets.compare_digest`. Gagal → `401` dengan header `WWW-Authenticate: ApiKey`.
- Auth dipasang **di level router** (`include_router(..., dependencies=[Depends(require_api_key)])`), bukan per endpoint.
- Semua key setara: tidak ada konsep kepemilikan gambar.

**Alternatif:**
- Tanpa auth (hanya didokumentasikan).
- API key dengan kepemilikan (`owner` per key, setiap query difilter).
- User + JWT + kepemilikan.

**Alasan:**
- API key adalah perlindungan paling sederhana yang tetap nyata: mencegah akses anonim tanpa menambah tabel user, hashing password, atau pengelolaan token.
- `APIKeyHeader` bawaan FastAPI membuat Swagger otomatis menampilkan tombol **Authorize**, jadi reviewer tetap bisa mencoba semua endpoint dari browser.
- `compare_digest` mencegah *timing attack* (menebak key dari selisih waktu respons).
- Fail closed mencegah aplikasi tidak sengaja berjalan tanpa perlindungan karena konfigurasi lupa diisi.
- Pemasangan di level router berarti endpoint baru di router itu otomatis terlindungi. Ditambah test yang memeriksa **setiap** route di aplikasi, risiko lupa memasang auth jadi kecil.

**Konsekuensi:**
- Semua pemegang key bisa melihat dan menghapus semua gambar. Kepemilikan per key bisa ditambahkan nanti tanpa mengubah cara auth bekerja (cukup menambah kolom `owner` dan filter di query).
- Download (`/images/{id}/file`, thumbnail) juga butuh header, sehingga tag `<img src="...">` di browser tidak bisa langsung memuat gambar. Bisa diterima karena tidak ada frontend dalam cakupan. Solusi nanti: URL bertanda tangan (*signed URL*) yang berlaku singkat.
- Key tidak pernah di-log. `.env` masuk `.gitignore`; hanya `.env.example` dengan nilai contoh yang di-commit.
- Rotasi key dilakukan dengan menambah key baru ke `API_KEYS`, memindahkan client, lalu menghapus key lama (restart diperlukan).

---

### ADR-17: Metadata dihapus dari file asli

**Keputusan:** setelah file lolos `verify()` dan **sebelum** dipindahkan ke lokasi final, file asli disimpan ulang tanpa metadata (EXIF termasuk GPS, XMP, komentar, chunk teks PNG). Orientasi EXIF diterapkan dulu ke piksel. Profil warna ICC dipertahankan.

| Format | Cara | Kualitas |
|---|---|---|
| JPEG | `exif_transpose`, simpan ulang dengan `quality="keep"` | penurunan minimal (tabel kuantisasi asli dipakai ulang) |
| PNG | simpan ulang tanpa `pnginfo` / `eXIf` | lossless |
| GIF | simpan ulang dengan `save_all=True` | lossless |
| WebP | simpan ulang dengan `save_all=True`; lossless jika sumbernya lossless, selain itu `quality=90` | lossless atau mendekati |

**Alternatif:**
- Membiarkan file asli apa adanya (hanya thumbnail yang bersih).
- Menghapus metadata di worker Celery.
- Menghapus hanya tag GPS.

**Alasan:**
- Foto dari ponsel sering berisi **lokasi GPS**, model perangkat, dan waktu pengambilan. `GET /images/{id}/file` menyajikan file asli, jadi tanpa langkah ini data tersebut bocor ke siapa pun yang mengunduhnya.
- Dilakukan di API, bukan di worker: jika di worker, file dengan GPS sudah tersimpan dan bisa diunduh selama task belum berjalan. Ukuran file maks. 1 MB, jadi biayanya kecil (tetap di threadpool, lihat ADR-12).
- Menghapus semua metadata lebih aman daripada memilih tag tertentu: daftar tag sensitif mudah terlewat.
- Orientasi diterapkan ke piksel dulu, karena tanpa tag orientasi foto ponsel akan tampil miring.
- Bonus: menyimpan ulang juga membuang data tersembunyi di file asli (misalnya file *polyglot*), sama seperti thumbnail (ADR-11).

**Konsekuensi:**
- File yang disimpan tidak identik byte-per-byte dengan file yang di-upload. `size_bytes`, `width`, dan `height` dicatat **setelah** pembersihan.
- JPEG yang perlu diputar akan di-encode ulang sepenuhnya (sedikit penurunan kualitas). Bisa diterima demi tampilan yang benar.
- Metadata yang berguna (misalnya hak cipta di EXIF) ikut hilang.

---

### ADR-18: Sesi upload kedaluwarsa 10 menit, dibersihkan saat startup

**Keputusan:**
- Setiap Upload punya `expires_at = created_at + 10 menit` (setting `UPLOAD_SESSION_TTL_SECONDS=600`). `POST /uploads/{id}/files` pada sesi kedaluwarsa → `410 Gone`.
- Kedaluwarsa hanya dicek **saat request file dimulai**. Upload yang sudah berjalan tidak diputus.
- Saat aplikasi start (lifespan), `cleanup_abandoned(now)` berjalan:
  1. Hapus Upload yang kedaluwarsa **dan tidak punya gambar** (sesi kosong, gagal, atau semua file-nya ditolak).
  2. Upload yang kedaluwarsa tetapi masih `uploading` (proses mati di tengah upload) → `failed`. Gambar yang sudah diterima tetap ada.
  3. Hapus file temp yang lebih tua dari 10 menit.
  4. Log ringkasan hasilnya.

**Alternatif:**
- Tanpa kedaluwarsa.
- Cleanup terjadwal (Celery beat atau loop latar belakang).
- Cleanup setiap kali `POST /uploads` dipanggil.

**Alasan:**
- Tanpa kedaluwarsa, siapa pun yang punya key bisa membuat sesi kosong tanpa batas dan memenuhi DB.
- 10 menit jauh lebih lama dari waktu yang dibutuhkan untuk upload maks. ~3 MB, tetapi cukup singkat agar sesi tidak bertahan lama.
- Cleanup saat startup paling sederhana: tidak perlu proses tambahan (Celery beat) di Windows, dan juga membereskan sisa crash sebelumnya (status `uploading` yang menggantung, file temp yatim).
- Sesi yang punya gambar **tidak** dihapus, karena Upload adalah induk dari Image. Menghapusnya akan menghapus data milik user.
- `now` di-inject ke fungsi cleanup agar bisa di-test tanpa menunggu 10 menit.

**Konsekuensi:**
- Selama server terus berjalan, sesi kedaluwarsa tetap ada di DB sampai restart berikutnya (tetapi tidak bisa dipakai karena `410`). Cleanup terjadwal dicatat sebagai pekerjaan produksi (ADR-19).
- Semua waktu disimpan dalam UTC agar perbandingan kedaluwarsa konsisten.
- Cleanup hanya menyentuh data yang lebih tua dari 10 menit, sehingga aman walaupun proses API lain sedang menerima upload.

---

### ADR-19: Keamanan yang ditunda ke produksi

**Keputusan:** hal-hal berikut **tidak diimplementasikan** dalam proyek ini, tetapi dicatat di sini dan di README sebagai pekerjaan produksi.

| Item | Risiko jika tidak ada | Rencana untuk produksi |
|---|---|---|
| **Rate limiting** | Spam pembuatan sesi atau upload dari satu client | Batas per API key dan per IP (misalnya `slowapi` dengan Redis, atau di API gateway / reverse proxy) |
| **CORS** | Saat ini tidak ada frontend; tanpa konfigurasi, browser dari domain lain memang tidak bisa memanggil API (aman secara default) | `CORSMiddleware` dengan allowlist domain dari setting; jangan pernah `allow_origins=["*"]` bersama kredensial |
| Header download (`X-Content-Type-Options: nosniff`, `Content-Disposition`) | Browser bisa "menebak" tipe file | Tambahkan pada response file dan thumbnail |
| Batas waktu upload lambat (*slowloris*) | Koneksi yang mengirim sangat lambat menahan sumber daya | Batas waktu total pada loop stream → `408` |
| Batas jumlah frame GIF | GIF kecil dengan ribuan frame memakan CPU/memori | Tolak GIF di atas N frame |
| Batas jumlah bagian / ukuran header multipart | Ribuan field kecil dalam satu request | Batasi di `MultipartStreamHandler` |
| Penghitungan total byte sebagai cadangan `Content-Length` | Bergantung pada server (uvicorn) untuk menegakkan `Content-Length` | Hitung total di loop stream juga |
| Normalisasi nama file tampilan | Nama sangat panjang atau karakter kontrol masuk ke log/response | NFKC, buang karakter kontrol, maks. 255 karakter |
| Cleanup terjadwal | Sesi kedaluwarsa menunggu restart | Celery beat atau tugas berkala |
| Menonaktifkan `/docs` | Dokumentasi API terbuka untuk umum | Setting untuk mematikan Swagger di produksi |

**Alasan:** cakupan take-home dijaga tetap fokus. Item yang diimplementasikan (ADR-16 s.d. ADR-18) dipilih karena menutup risiko terbesar: akses anonim, kebocoran lokasi pengguna, dan penumpukan data. Rate limiting dan CORS paling tepat ditangani bersama infrastruktur produksi (Redis, reverse proxy, domain frontend yang sudah pasti), yang belum ada di proyek ini.

**Konsekuensi:** dokumen ini dan README menjadi daftar periksa yang jelas sebelum service dipakai di lingkungan produksi.

---

## 4. Keterbatasan yang diketahui

| Keterbatasan | Dampak | Jalan keluar di masa depan |
|---|---|---|
| Broker SQLite experimental dan berbasis polling | Ada jeda kecil sebelum task diambil; tidak untuk produksi | Ganti ke Redis/RabbitMQ (hanya konfigurasi) |
| `--pool=solo` | Satu gambar diproses dalam satu waktu | `--pool=threads` atau worker Linux |
| Crash di antara commit dan `delay()` | Gambar bisa tertinggal di `queued` | Pola outbox / job pemindai ulang |
| Polling | Ada jeda antara perubahan dan tampilan | SSE/WebSocket |
| Disk lokal | API dan worker harus di mesin yang sama | Antarmuka storage → S3/MinIO |
| Tanpa Alembic | Perubahan skema = hapus DB lokal | Tambahkan Alembic |
| API key tanpa kepemilikan | Semua pemegang key bisa membaca/menghapus semua gambar | Kolom `owner` per key + filter di setiap query |
| Download butuh header API key | `<img src>` di browser tidak bisa langsung memuat gambar | Signed URL berumur pendek |
| Cleanup hanya saat startup | Sesi kedaluwarsa tetap di DB sampai restart | Cleanup terjadwal |
| Rate limiting, CORS, dan item lain di ADR-19 | Lihat tabel ADR-19 | Lihat tabel ADR-19 |
