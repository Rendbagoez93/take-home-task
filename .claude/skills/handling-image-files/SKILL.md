---
name: handling-image-files
description: Use when writing or changing services/validation.py, sanitize.py, thumbnails.py, storage.py, or cleanup.py, or any code that opens, verifies, re-saves, strips EXIF/GPS metadata from, resizes, moves, or deletes image files with Pillow.
---

# Handling Image Files

## Overview
Client data (filename, extension, Content-Type) is untrusted. Only Pillow decoding proves a file is an image. Everything here is **pure functions over paths**, testable without HTTP or Celery (ADR-10, 11, 13, 17).

## Validation order (cheapest first, ADR-10)
filename present → file count → extension → Content-Type → magic bytes → size ≤ 1 MB → not empty → Pillow `verify()` + pixel limit → format matches extension.

| Format | Magic bytes |
|---|---|
| JPEG | `FF D8 FF` |
| PNG | `89 50 4E 47 0D 0A 1A 0A` |
| GIF | `GIF87a` / `GIF89a` |
| WebP | `RIFF` at 0-3 and `WEBP` at 8-11 |

`verify()` leaves the image unusable, so **re-open** the file to read size and format. Reject when `width * height > settings.max_image_pixels`, and also catch `Image.DecompressionBombError`. Store the format Pillow detected, never the client's Content-Type.

## Stripping metadata (`sanitize.py`, ADR-17)
Write to a second temp file, then `os.replace` over the first. Keep `icc_profile`; drop everything else.

| Format | How |
|---|---|
| JPEG | Orientation tag (`getexif().get(0x0112, 1)`) is 1 → save the **original object** with `quality="keep"`, `subsampling="keep"`. Otherwise → `ImageOps.exif_transpose`, then save with an explicit quality (`"keep"` raises on a non-JPEG copy). |
| PNG | Save with no `pnginfo` and no `exif` |
| GIF | `save_all=True`, keep `duration` and `loop`, no `comment` |
| WebP | `save_all=True`; `lossless=True` if the source is lossless (first image chunk is `VP8L`), else `quality=90` |

Some Pillow plugins fall back to `im.info` for `exif`, `xmp`, or `comment`. Remove those keys before saving. The tests (EXIF empty, no PNG text chunks, same frame count, ICC kept) prove the result; reading the code does not.

## Thumbnails (`thumbnails.py`, ADR-11)
`make_thumbnails(src, sizes, out_dir, on_progress=None) -> dict[int, Path]`
- Open → `exif_transpose` → frame 0 for GIFs → convert to `RGBA` if it has transparency, else `RGB`.
- For each size: `copy()` → `thumbnail((size, size))` → save `<uuid>_<size>.webp` (overwrite) → `on_progress(i, len(sizes))`.

## Storage and cleanup
- Stored names are `f"{uuid4()}.{ext}"`. **Never** build a path from the user's filename.
- `promote()` uses `os.replace` (atomic). Deletes use `Path.unlink(missing_ok=True)`.
- `cleanup_abandoned(db, now)` takes `now` as an argument and returns `{sessions_deleted, sessions_failed, temp_files_deleted}`. Only touch data older than the session TTL (ADR-18). Compare times in UTC.

## Common mistakes
- Trusting the extension or Content-Type after the magic-bytes check. The final word is Pillow's detected format.
- Stripping only GPS tags. Strip all metadata (ADR-17).
- Running sanitize in the worker. It must run in the API before promotion, or the GPS data is downloadable until the task runs.
- Recording `size_bytes`, `width`, `height` **before** sanitizing. Record them after.
- Calling `datetime.now()` inside cleanup. Inject `now`.
