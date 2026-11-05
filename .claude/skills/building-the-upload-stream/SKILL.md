---
name: building-the-upload-stream
description: Use when writing or changing MultipartStreamHandler, upload_service.py, the POST /uploads/{id}/files endpoint, upload progress (bytes_received) throttling, or anything that reads request.stream() or uses python-multipart's MultipartParser.
---

# Building the Upload Stream

## Overview
The upload endpoint reads the raw body as a stream so it can count bytes and reject files *while* they arrive (ADR-02, ADR-03, ADR-08). Keep the parsing logic **sync and I/O-free** so it can be unit-tested. The async endpoint does the I/O.

## Shape

```python
# services/multipart_stream.py: sync, no disk, no DB
class MultipartStreamHandler:
    def __init__(self, content_type: str, settings: Settings): ...
    def feed(self, chunk: bytes) -> list[PartEvent]: ...  # parser.write(chunk); return queued events
    def finish(self) -> list[PartEvent]: ...

# endpoint / upload_service.py: async loop, every I/O call goes through a thread
async for chunk in request.stream():
    events = handler.feed(chunk)
    await anyio.to_thread.run_sync(apply_events, events)  # write temp files, verify, sanitize
    received += len(chunk)
    if should_write_progress(received, last_written, expected):
        await anyio.to_thread.run_sync(save_bytes_received, upload_id, received)
```

`PartEvent` is a small dataclass, for example `kind` ("data" | "end" | "rejected"), `index`, `data`, `code`. Parser callbacks only update in-memory part state and append events.

## Quick reference

| Concern | Rule | Source |
|---|---|---|
| Parser | `python_multipart.multipart.MultipartParser(boundary, callbacks)`; the callback data is `data[start:end]` | ADR-02 |
| Boundary | from the request `Content-Type` via `parse_options_header` | PLAN F4 |
| Pre-checks, in order | 404 session → 410 expired → 409 not `created` → 411 no Content-Length → 413 over `3 × 1 MB + overhead` | PLAN F5 |
| Non-file field | ignore it (no filename) | PLAN §7 |
| 4th+ file | `too_many_files`; discard its bytes, write nothing | ADR-08 |
| Header checks | extension + Content-Type allowlist at headers end | ADR-10 |
| Magic bytes | check after ≥12 bytes are buffered; never write before the check passes | ADR-10 |
| Size | over 1 MB → `too_large`; stop writing, delete temp, keep parsing | ADR-08 |
| End of part | empty → `empty_file`; else verify → strip metadata → promote | ADR-10, 17 |
| Progress write | when it grew ≥ 64 KB **or** ≥ 5 % since the last write, plus one final write | ADR-03 |
| Accepted file | promote → insert `Image(queued)` → **commit** → `.delay(image_id)` → save `task_id` | ADR-09 |
| Cleanup | `try/finally`: on `ClientDisconnect` or any error, delete all temp files and set `Upload.status = failed` | ADR-09 |
| Swagger | add the request body by hand via `openapi_extra` (see PLAN F5) | ADR-02 |

## Common mistakes
- Taking `UploadFile` or `File(...)` as a parameter. FastAPI then buffers the whole body; take `request: Request` instead.
- Doing disk, Pillow, or DB work directly in the `async` loop. That blocks every request on the server (ADR-12).
- Doing I/O inside parser callbacks. It makes the handler untestable without a filesystem.
- Assuming a boundary or the 12-byte magic header arrives in one chunk. Test with chunk sizes 1, 7, and the whole body.
- Calling `.delay()` before the commit. The worker can start before the row exists.
- Keeping progress in a module-level `dict`. Other processes can't see it; it must go in the DB (ADR-03).
- Checking expiry mid-stream. Check it only at request start (ADR-18).
