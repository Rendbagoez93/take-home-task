---
name: testing-the-service
description: Use when writing or fixing tests under tests/, editing conftest.py fixtures, generating test images, or when pytest fails, is flaky, leaves files in data/, or tests interfere with each other in the image upload service.
---

# Testing the Service

## Overview
The risky parts (stream parsing, validation, sanitizing) are pure functions, so they get deterministic unit tests. The API is tested with `TestClient` and eager Celery. No real server, no real worker, and no binary files in the repo (ADR-15).

## conftest essentials
- **Isolated data dir:** every test gets settings pointing at `tmp_path` (DB, uploads, temp, thumbnails). Build the app with the factory: `create_app(test_settings)`.
- **Celery eager:** `task_always_eager=True`, `task_eager_propagates=True`, `result_backend="cache+memory://"`, set before the first task runs.
- **Auth:** a fixed test key in `API_KEYS`; the `client` fixture sends `X-API-Key` by default.
- **Images made by Pillow at runtime**, written to `tmp_path`:

| Fixture | How |
|---|---|
| valid PNG/JPEG/GIF/WebP | `Image.new(...).save(path, fmt)` |
| RGBA PNG | `Image.new("RGBA", ...)` |
| JPEG rotated by EXIF + GPS | build an `Image.Exif()` with `0x0112` (Orientation) and a GPS IFD (`0x8825`), then `save(..., exif=exif)` |
| PNG with text chunk | `PngImagePlugin.PngInfo().add_text(...)` |
| animated GIF | `save_all=True, append_images=[...]` |
| text renamed `.png` | write `b"hello"` |
| truncated PNG | valid PNG bytes `[:-20]` |
| empty file | `b""` |
| 1 MB + 1 byte | valid header padded to `settings.max_file_bytes + 1` |
| pixel bomb | PNG header declaring huge width/height |

## Patterns
- **Multipart stream:** build the body by hand and feed it to `MultipartStreamHandler` in chunk sizes `1`, `7`, and the whole body. Use `pytest.mark.parametrize` over chunk sizes.
- **Time:** pass `now` explicitly (`cleanup_abandoned(db, now=...)`). Never sleep.
- **Auth sweep:** loop over `app.routes`, skip the public set, and assert each route returns `401` without a key.
- **Metadata stripped:** after upload, open the stored original and assert `not img.getexif()` and no `0x8825`.

## Commands
- `uv run pytest` runs everything; add `-x -q` to stop at the first failure.
- `uv run pytest tests/test_multipart_stream.py -k chunk` runs a focused subset.

## Common mistakes
- Tests writing into the real `data/` folder. Every path must come from the test settings.
- Sharing a DB or app between tests. Use function-scoped fixtures.
- Asserting on Celery `AsyncResult` state in eager mode. Assert the DB status instead.
- Committing `.png`/`.jpg` fixtures. Generate them.
- Mocking Pillow or the parser. Test with real bytes; that is the point of these tests.
