# Image Upload Service

Service FastAPI untuk meng-upload gambar dengan progress yang bisa dipantau. Anda membuka sesi upload, melakukan streaming hingga 3 gambar (maks. 1 MB per file) dalam satu request, lalu melakukan polling ke satu endpoint untuk melihat dua jenis progress:

- **Progress upload**: jumlah byte yang sudah diterima server.
- **Progress pemrosesan**: satu task Celery per gambar membuat 3 thumbnail WebP (sisi terpanjang 128, 256, dan 512 px).

Service ini juga menyediakan CRUD gambar, autentikasi API key, dan menghapus metadata EXIF serta GPS dari setiap file asli yang disimpan.

Keputusan desain ada di [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (ADR-01 s.d. ADR-19), dan rencana implementasi ada di [docs/PLAN.md](docs/PLAN.md).

## Prasyarat

- Python 3.14
- [uv](https://docs.astral.sh/uv/)

Service ini tidak membutuhkan Docker, Redis, atau RabbitMQ. Semua data disimpan di file SQLite dan disk lokal di folder `data/`. Service dikembangkan dan diuji di Windows 11.

## Menyiapkan proyek

1. Install dependensi:

   ```bash
   uv sync
   ```

2. Buat file `.env` dari contoh:

   ```bash
   cp .env.example .env              # bash
   Copy-Item .env.example .env       # PowerShell
   ```

3. Buat API key, lalu isi sebagai `API_KEYS` di `.env`:

   ```bash
   uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   ```ini
   API_KEYS=tempel-key-hasil-perintah-di-atas
   ```

   Untuk beberapa key, pisahkan dengan koma. Jika `API_KEYS` kosong, API dan worker menolak untuk start. `.env` masuk `.gitignore`; jangan pernah meng-commit file ini.

## Menjalankan service

Jalankan API dan worker di dua terminal dari root proyek.

1. Jalankan API:

   ```bash
   uv run fastapi dev main.py
   ```

   API berjalan di `http://127.0.0.1:8000`. Saat startup, API membuat folder dan tabel di `data/`, menghapus sesi upload kosong yang sudah kedaluwarsa serta file temp basi, lalu mencatat ringkasannya di log:

   ```text
   INFO:main:Startup cleanup: {'sessions_deleted': 0, 'sessions_failed': 0, 'temp_files_deleted': 0}
   ```

2. Jalankan worker Celery:

   ```bash
   uv run celery -A app.worker.celery_app worker --pool=solo -l info
   ```

   `--pool=solo` wajib di Windows. Worker memproses satu gambar dalam satu waktu.

3. Pastikan API sudah berjalan. `/health` adalah satu-satunya endpoint yang tidak butuh key:

   ```bash
   curl http://127.0.0.1:8000/health
   ```

   ```json
   {"status":"ok"}
   ```

Jika worker tidak berjalan, upload tetap berhasil, tetapi gambar tetap berstatus `queued` sampai worker dijalankan.

## Meng-upload gambar

Upload selalu terdiri dari dua langkah:

1. **Buka sesi** dengan `POST /uploads`. Response-nya berisi `upload_id`.
2. **Kirim file** ke `POST /uploads/{upload_id}/files`, maksimal 3 gambar per request.

Setelah itu, pantau progress dengan `GET /uploads/{upload_id}`. Setiap request selain `/health` wajib membawa header `X-API-Key` yang berisi salah satu key dari `API_KEYS` di `.env`.

Pastikan API dan worker sudah berjalan (lihat [Menjalankan service](#menjalankan-service)) sebelum mencoba salah satu cara di bawah.

### Lewat Swagger (paling mudah)

1. Buka `http://127.0.0.1:8000/docs` di browser.
2. Klik **Authorize** di kanan atas, tempel key Anda di field `X-API-Key`, klik **Authorize**, lalu klik **Close**.
3. Buka **POST /uploads**, klik **Try it out**, lalu klik **Execute**. Salin nilai `upload_id` dari response.
4. Buka **POST /uploads/{upload_id}/files**, klik **Try it out**, lalu tempel `upload_id` tersebut.
5. Di field `files`, pilih hingga 3 gambar. Klik **Add string item** untuk menambah file kedua dan ketiga.
6. Klik **Execute**. Response `202` berisi daftar file yang diterima (`accepted`) dan yang ditolak (`rejected`).
7. Buka **GET /uploads/{upload_id}**, tempel `upload_id`, lalu klik **Execute** beberapa kali. Status gambar akan berubah dari `queued` ke `processing`, lalu `done`.

Swagger tidak bisa menampilkan progress bar. Progress terlihat dari angka yang berubah setiap kali Anda melakukan polling.

### Lewat curl (bash)

```bash
KEY=api-key-anda
BASE=http://127.0.0.1:8000
```

1. Buka sesi upload. Sesi kedaluwarsa 10 menit setelah dibuat.

   ```bash
   curl -X POST -H "X-API-Key: $KEY" $BASE/uploads
   ```

   ```json
   {"upload_id":"846622a0-6fe1-4730-bc32-20684048ec76","status":"created","expires_at":"2026-11-05T09:07:08.894576Z"}
   ```

2. Kirim hingga 3 file dalam satu request. Setiap file diterima atau ditolak secara terpisah:

   ```bash
   UPLOAD_ID=846622a0-6fe1-4730-bc32-20684048ec76
   curl -H "X-API-Key: $KEY" \
     -F "files=@cat.jpg" -F "files=@dog.png" -F "files=@notes.txt" \
     $BASE/uploads/$UPLOAD_ID/files
   ```

   ```json
   {
     "upload_id": "846622a0-6fe1-4730-bc32-20684048ec76",
     "accepted": [
       {"image_id": "5a87e6d8-e651-4371-a072-bf9921343bca", "filename": "cat.jpg"},
       {"image_id": "5a7600f9-aaa7-4e69-ab66-022835bd5f2a", "filename": "dog.png"}
     ],
     "rejected": [
       {"filename": "notes.txt", "reason": "Extension .txt is not allowed", "code": "unsupported_type"}
     ]
   }
   ```

   Satu sesi hanya menerima satu request file. Request kedua pada sesi yang sama mengembalikan `409`; buka sesi baru untuk upload lagi.

3. Lakukan polling ke sesi untuk melihat progress upload dan pemrosesan:

   ```bash
   curl -H "X-API-Key: $KEY" $BASE/uploads/$UPLOAD_ID
   ```

   ```json
   {
     "upload_id": "846622a0-6fe1-4730-bc32-20684048ec76",
     "status": "completed",
     "upload": {"current": 17191, "total": 17191, "percent": 100.0},
     "images": [
       {"image_id": "5a87e6d8-…", "filename": "cat.jpg", "status": "processing",
        "progress": {"current": 1, "total": 3, "percent": 33.3}},
       {"image_id": "5a7600f9-…", "filename": "dog.png", "status": "done",
        "progress": {"current": 3, "total": 3, "percent": 100.0}}
     ],
     "rejected": [
       {"filename": "notes.txt", "reason": "Extension .txt is not allowed", "code": "unsupported_type"}
     ]
   }
   ```

   Status gambar berjalan `queued → processing → done`, atau berakhir di `failed`. Untuk gambar `failed`, field `error` di `GET /images/{image_id}` berisi pesan umum; detail teknisnya (yang bisa memuat path di server) hanya dicatat di log worker.

### Lewat PowerShell

Di PowerShell, `curl` adalah alias untuk `Invoke-WebRequest`, sehingga contoh bash di atas akan gagal dengan error `A parameter cannot be found that matches parameter name 'X'`. Panggil `curl.exe` secara eksplisit:

```powershell
$KEY  = "api-key-anda"
$BASE = "http://127.0.0.1:8000"

# 1. Buka sesi upload dan simpan upload_id ke variabel
$UPLOAD_ID = (curl.exe -s -X POST -H "X-API-Key: $KEY" "$BASE/uploads" | ConvertFrom-Json).upload_id
$UPLOAD_ID

# 2. Kirim hingga 3 gambar. Path bisa absolut atau relatif terhadap folder saat ini.
curl.exe -H "X-API-Key: $KEY" `
  -F "files=@C:\Users\Anda\Pictures\cat.jpg" `
  -F "files=@C:\Users\Anda\Pictures\dog.png" `
  "$BASE/uploads/$UPLOAD_ID/files"

# 3. Pantau progress. Jalankan ulang perintah ini sampai semua gambar berstatus done.
curl.exe -H "X-API-Key: $KEY" "$BASE/uploads/$UPLOAD_ID"
```

Response-nya sama dengan contoh bash di atas. Contoh di bagian [Mengelola gambar dengan curl](#mengelola-gambar-dengan-curl) juga bisa dipakai di PowerShell dengan cara yang sama: ganti `curl` dengan `curl.exe`, isi variabel dengan `$IMAGE_ID = "..."`, dan ganti `\` di akhir baris dengan `` ` ``.

### Jika ada masalah

| Gejala | Penyebab | Solusi |
|---|---|---|
| `401 unauthorized` | Header `X-API-Key` tidak ada atau key salah | Pakai nilai dari `API_KEYS` di `.env`. Jika `.env` diubah setelah API berjalan, restart API, karena key hanya dibaca saat startup. |
| `409 conflict` | Sesi sudah dipakai untuk mengirim file | Setiap sesi hanya menerima satu request file. Buka sesi baru dengan `POST /uploads`. |
| `410 gone` | Sesi lebih dari 10 menit | Buka sesi baru dengan `POST /uploads`. |
| `413 content_too_large` | Total body melebihi batas 3 file × 1 MB | Kirim file yang lebih kecil atau lebih sedikit. |
| File masuk ke `rejected` | File bukan gambar yang valid, lebih dari 1 MB, atau kosong | Lihat `code` dan [Kode penolakan file](#kode-penolakan-file). |
| Gambar tetap `queued` | Worker Celery tidak berjalan | Jalankan worker di terminal kedua. Broker SQLite berbasis polling, jadi task baru mulai setelah jeda singkat. |
| Thumbnail mengembalikan `404` | Worker belum selesai membuat thumbnail | Tunggu sampai status gambar `done`. |

## Mengelola gambar dengan curl

```bash
IMAGE_ID=5a87e6d8-e651-4371-a072-bf9921343bca

# Daftar gambar: skip, limit (1-100, default 20), filter opsional upload_id dan status
curl -H "X-API-Key: $KEY" "$BASE/images?status=done&limit=10"

# Baca metadata dan URL thumbnail
curl -H "X-API-Key: $KEY" $BASE/images/$IMAGE_ID

# Unduh file asli (metadata sudah dihapus) dan thumbnail (128, 256, atau 512)
curl -H "X-API-Key: $KEY" -o original.jpg $BASE/images/$IMAGE_ID/file
curl -H "X-API-Key: $KEY" -o thumb.webp $BASE/images/$IMAGE_ID/thumbnails/256

# Ubah judul dan deskripsi
curl -X PATCH -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"title": "Sleepy cat", "description": "On the sofa"}' $BASE/images/$IMAGE_ID

# Hapus gambar beserta file asli dan thumbnail-nya (mengembalikan 204)
curl -X DELETE -H "X-API-Key: $KEY" $BASE/images/$IMAGE_ID
```

Thumbnail mengembalikan `404` sampai worker selesai membuatnya.

## Referensi API

| Method | Path | Hasil sukses |
|---|---|---|
| `GET` | `/health` | `200`, tanpa key |
| `POST` | `/uploads` | `201` sesi baru |
| `POST` | `/uploads/{upload_id}/files` | `202` daftar file yang diterima dan ditolak |
| `GET` | `/uploads/{upload_id}` | `200` progress upload dan pemrosesan |
| `GET` | `/images` | `200` halaman gambar: `items`, `total`, `skip`, `limit` |
| `GET` | `/images/{image_id}` | `200` metadata dan URL thumbnail |
| `GET` | `/images/{image_id}/file` | `200` byte file asli, dengan content-type hasil deteksi Pillow |
| `GET` | `/images/{image_id}/thumbnails/{size}` | `200` thumbnail WebP |
| `PATCH` | `/images/{image_id}` | `200` gambar yang sudah diubah (`title` ≤ 100 karakter, `description` ≤ 1000 karakter) |
| `DELETE` | `/images/{image_id}` | `204` |

### Error

Semua error mengembalikan bentuk JSON yang sama. `code` adalah nama status HTTP dalam snake case, kecuali `422` yang memakai `validation_error`:

```json
{"detail": "Image not found", "code": "not_found"}
```

Untuk error validasi request (`422`), `detail` berisi daftar error per field:

```json
{"detail": [{"type": "extra_forbidden", "loc": ["body", "owner"], "msg": "Extra inputs are not permitted", "input": "me"}], "code": "validation_error"}
```

| Status | Code | Penyebab |
|---|---|---|
| `400` | `bad_request` | Body bukan `multipart/form-data` yang valid (tanpa boundary, format rusak, atau terpotong) |
| `401` | `unauthorized` | Header `X-API-Key` tidak ada atau salah |
| `404` | `not_found` | Id upload atau gambar tidak dikenal, atau thumbnail belum siap |
| `409` | `conflict` | Sesi upload sudah dipakai |
| `410` | `gone` | Sesi upload sudah kedaluwarsa |
| `411` | `length_required` | Request file tidak punya `Content-Length` |
| `413` | `content_too_large` | Body request lebih besar dari 3 × 1 MB ditambah 64 KB untuk header multipart |
| `422` | `validation_error` | Parameter query, path, atau body tidak valid |
| `500` | `internal_server_error` | Error tak terduga; detailnya hanya dicatat di log server |

### Kode penolakan file

File yang ditolak tidak menggagalkan request. File tersebut masuk ke `rejected` dengan salah satu kode berikut:

| Code | Penyebab |
|---|---|
| `unsupported_type` | Ekstensi, `Content-Type`, atau signature file bukan JPEG, PNG, GIF, atau WebP |
| `too_large` | File lebih besar dari 1 MB |
| `empty_file` | File tidak berisi byte apa pun |
| `invalid_image` | Pillow tidak bisa men-decode file, file melewati batas jumlah piksel, atau format aslinya tidak sesuai ekstensi |
| `too_many_files` | Request berisi lebih dari 3 file; file ke-4 dst. dibuang |

## Menjalankan test

```bash
uv run pytest
```

Test menjalankan Celery dalam mode eager dan menulis ke folder sementara, sehingga tidak butuh worker dan tidak menyentuh `data/`. Gambar test dibuat dengan Pillow; repository tidak berisi fixture biner.

## Catatan desain

- **Upload dua langkah (ADR-01).** `POST /uploads` mengembalikan `upload_id` sebelum ada byte yang dikirim, sehingga client sudah punya id untuk polling selama request file masih berjalan.
- **Parsing raw stream (ADR-02).** Endpoint file membaca `request.stream()` dan mem-parsing-nya dengan `MultipartParser` dari `python-multipart`. `UploadFile` milik FastAPI akan mem-buffer seluruh body lebih dulu sehingga tidak ada progress upload yang bisa dilaporkan. Server menghitung byte saat diterima dan menulis jumlahnya ke SQLite paling sering setiap 64 KB atau 5 %.
- **Validasi berlapis (ADR-10).** Setiap file melewati pengecekan murah lebih dulu (ekstensi, `Content-Type`, signature file, ukuran), lalu decoding penuh dengan Pillow dan batas *decompression bomb*. Content-type yang disimpan adalah hasil deteksi Pillow, bukan header dari client. File disimpan dengan nama UUID, tidak pernah dengan nama file dari client.
- **Penghapusan metadata (ADR-17).** Sebelum file disimpan, orientasi EXIF diterapkan ke piksel, lalu gambar disimpan ulang tanpa EXIF, GPS, komentar, atau chunk teks PNG. Profil warna ICC dipertahankan.
- **Urutan penulisan yang konsisten (ADR-09).** File ditulis ke path temp, diverifikasi, lalu dipindahkan dengan `os.replace` (atomic). Baris `Image` di-commit sebelum task Celery diantrekan, sehingga worker tidak pernah mencari baris yang belum ada. Saat client terputus atau terjadi error, file temp dari request itu dihapus dan sesi ditandai `failed`.
- **DB adalah sumber kebenaran (ADR-07).** `Image.status` adalah catatan permanen. Meta task Celery hanya dibaca untuk `current/total` saat gambar berstatus `processing`, karena hasil Celery bisa kedaluwarsa.
- **SQLite di semua tempat (ADR-05, ADR-06).** Celery memakai transport SQLAlchemy dari kombu dan DB result backend, di file yang terpisah dari `app.db`. Semua database memakai mode WAL dengan busy timeout, dan task melakukan retry saat `OperationalError` ("database is locked").
- **Async hanya jika perlu (ADR-12).** Endpoint streaming bersifat `async` dan menjalankan pekerjaan disk, Pillow, dan DB di thread. Endpoint lainnya memakai `def` biasa.
- **Auth API key (ADR-16).** Auth dipasang sekali per router, dan ada test yang memastikan setiap route selain `/health`, `/docs`, dan `/openapi.json` mengembalikan `401` tanpa key. Key dibandingkan dengan `secrets.compare_digest` dan tidak pernah di-log.

## Keterbatasan

- Broker SQLite berstatus experimental dan berbasis polling, sehingga task mulai dengan sedikit jeda. Tidak ditujukan untuk produksi.
- `--pool=solo` memproses satu gambar dalam satu waktu.
- Jika proses API mati di antara commit gambar dan pengantrean task-nya, gambar tertinggal di `queued`.
- API dan worker harus berbagi disk yang sama, karena file disimpan secara lokal.
- Tidak ada migrasi. Untuk mengubah skema, hapus folder `data/` lokal.
- Setiap key yang valid bisa membaca dan menghapus semua gambar; key tidak punya pemilik.
- Download butuh header `X-API-Key`, sehingga tag `<img src>` di browser tidak bisa langsung memuatnya.
- Sesi kedaluwarsa hanya dibersihkan saat API start.

## Keamanan untuk produksi

Item berikut dicatat di ADR-19 dan **belum diimplementasikan**. Tangani sebelum service dipakai di produksi.

| Item | Risiko jika tidak ada | Rencana |
|---|---|---|
| Rate limiting | Satu client bisa membanjiri service dengan sesi atau upload | Batas per API key dan per IP, di aplikasi (misalnya `slowapi` dengan Redis) atau di gateway / reverse proxy |
| CORS | Saat ini aman: tanpa header CORS, browser dari domain lain tidak bisa memanggil API | `CORSMiddleware` dengan allowlist domain dari setting; jangan pernah `allow_origins=["*"]` bersama kredensial |
| Header download | Browser bisa "menebak" tipe file | Tambahkan `X-Content-Type-Options: nosniff` dan `Content-Disposition` pada response file dan thumbnail |
| Batas waktu upload lambat | Client yang mengirim sangat lambat menahan sumber daya (*slowloris*) | Batas waktu total pada loop stream yang mengembalikan `408` |
| Batas jumlah frame GIF | GIF kecil dengan ribuan frame memakan banyak CPU dan memori | Tolak GIF di atas jumlah frame tertentu |
| Batas jumlah bagian dan ukuran header multipart | Request berisi ribuan field kecil | Batasi di `MultipartStreamHandler` |
| Penghitungan byte sebagai cadangan `Content-Length` | Batas ukuran bergantung pada server yang menegakkan `Content-Length` | Hitung total byte di loop stream juga |
| Normalisasi nama file tampilan | Nama sangat panjang atau karakter kontrol masuk ke log dan response | NFKC, buang karakter kontrol, maks. 255 karakter |
| Cleanup terjadwal | Sesi kedaluwarsa menunggu restart | Celery beat atau tugas berkala lain |
| Menonaktifkan `/docs` | Dokumentasi API terbuka untuk umum | Setting untuk mematikan Swagger di produksi |
| Signed URL untuk download | Download butuh header API key | Signed URL berumur pendek |
| Kepemilikan per key | Setiap key bisa melihat semua gambar | Kolom `owner` per key dan filter di setiap query |

## Struktur proyek

```text
main.py              app factory, router, exception handler, startup
app/
  config.py          setting (batas, ukuran, path, API_KEYS)
  security.py        pengecekan X-API-Key
  db.py models.py schemas.py
  api/               hanya HTTP: uploads.py, images.py
  services/          validation, sanitize, storage, cleanup, multipart_stream,
                     upload_service, progress, thumbnails
  worker/            celery_app.py, tasks.py (tidak pernah meng-import app FastAPI)
tests/               satu file per service dan router, plus conftest.py
data/                database dan file runtime (masuk .gitignore)
```
