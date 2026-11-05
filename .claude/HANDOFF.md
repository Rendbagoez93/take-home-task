# Session handoff: after Phases 7 and 8

Read this first in a new session. Project rules (`CLAUDE.md`, `rules.md`), `docs/PLAN.md` and `docs/ARCHITECTURE.md` are still the sources of truth; this file holds only what they don't.

## Status (2026-11-05)

- All PLAN phases (0–8) are done. `uv run pytest`: **254 passed**.
- End-to-end run with real uvicorn + `--pool=solo` worker + SQLite broker (scratch data dir): every upload and image endpoint, 401/404/409/422 error bodies, `/docs` and `/openapi.json` 200 without key, `/redoc` 404, startup cleanup summary visible in the uvicorn log.
- Swagger itself was not clicked through in a browser; the same requests were made with curl.
- Nothing is committed. The repo has no commits yet, and every file is untracked on `master`.

## Phase 7/8: what was built

- `app/api/images.py`: list (`skip`, `limit` 1..`max_page_size`, `upload_id`, `status`; ordered by `created_at, id`), get, file download (`media_type=content_type`), thumbnail download (size not in `thumbnail_sizes` → 422, not ready → 404), PATCH (`exclude_unset`, `null` clears), DELETE (commit row first, then `delete_image_files`).
- New settings: `default_page_size=20`, `max_page_size=100`.
- `app/schemas.py`: `ErrorResponse {detail: str | list[dict], code: str}` and `error_responses(*codes)` for Swagger.
- Root `main.py` (moved from `app/main.py` at the user's request; run with `uv run fastapi dev main.py`): three handlers (HTTPException, RequestValidationError, Exception→500 with generic message, logged). `code` = HTTPStatus phrase in snake case (`not_found`, `content_too_large`, …), except 422 → `validation_error` (user's choice). 401 documented once via `include_router(responses=...)`; other statuses per route.
- `app/worker/tasks.py`: a failed task stores the fixed `THUMBNAIL_FAILED_MESSAGE` in `Image.error`, not `str(error)`, because Pillow messages contain server paths. The re-raised exception keeps the details in the worker log. PLAN F6 was updated to match.
- `logging.basicConfig(level=INFO)` in the lifespan, so the startup cleanup summary appears under uvicorn.
- `README.md` in Indonesian (user's request).

## Resolved decisions (2026-11-05, user: "best implementation according to the architecture")

- `RejectionCode` moved to `app/models.py`, next to `UploadStatus`/`ImageStatus` (it is the vocabulary of `Upload.rejected_files[].code`). `multipart_stream.py` no longer imports schemas. `upload_service.py` and `progress.py` still build response schemas; that is accepted, because `schemas` depends only on `models` (no cycle, and nothing imports `api`).
- `max_image_pixels = 40_000_000`: confirmed by the user.
- Allowed types stay as constants in `validation.py`: each format needs its own code (magic bytes, sanitize §7), so a setting could enable a format the code can't handle. PLAN §4 updated to match.
- Kept as built, all consistent with the ADRs: EXIF rotation on PNG/single-frame WebP (PLAN §7 is format-agnostic); `delete_image_files` globs `<uuid>_*.webp` (catches thumbnails a running task wrote but hadn't recorded yet, ADR-09); `autouse` `settings` fixture (test isolation, ADR-15); `redoc_url=None` (PLAN §6 lists only `/docs` and `/openapi.json` as public); 64 KB overhead for 413 (ADR-08); JPEGs stored as `.jpeg` (extension = detected format, lowercased).

No open questions remain.
