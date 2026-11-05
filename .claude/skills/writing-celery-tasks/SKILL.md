---
name: writing-celery-tasks
description: Use when writing or debugging worker/celery_app.py, worker/tasks.py, generate_thumbnails, reading task progress with AsyncResult, the SQLite broker or result backend, or when hitting "database is locked", tasks stuck in queued, or Celery failing to start on Windows.
---

# Writing Celery Tasks (SQLite broker, Windows)

## Overview
Celery only does processing: one task per image, receiving **only `image_id`** (ADR-04). The DB `Image.status` is the source of truth; Celery meta is read only for `current/total` while an image is `processing` (ADR-07).

## Config (`worker/celery_app.py`)

```python
celery_app = Celery("worker", broker=settings.celery_broker_url, backend=settings.celery_result_backend)
celery_app.conf.update(
    task_track_started=True,
    result_extended=True,
    result_expires=86400,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)
```
- Broker `sqla+sqlite:///data/celery-broker.db` and backend `db+sqlite:///data/celery-results.db`, in files separate from `app.db` (ADR-05).
- **Never import the FastAPI app or `app.api` from here** (ADR-13).
- If the kombu SQLAlchemy transport breaks with SQLAlchemy 2.1, pin `sqlalchemy<2.1` (Phase 0 fallback).

## Task shape

```python
@celery_app.task(bind=True, autoretry_for=(OperationalError,), retry_backoff=True, max_retries=5)
def generate_thumbnails(self, image_id: str) -> None:
    with SessionLocal() as db:                      # short session #1
        image = db.get(Image, image_id)
        if image is None or not original_path(image).exists():
            return
        image.status = "processing"
        db.commit()
        src = original_path(image)

    try:
        paths = make_thumbnails(src, settings.thumbnail_sizes, settings.thumbnails_dir,
                                on_progress=lambda i, n: self.update_state(
                                    state="PROGRESS", meta={"current": i, "total": n}))
    except OperationalError:
        raise                                       # let autoretry handle lock errors
    except Exception as exc:
        mark_failed(image_id, str(exc))
        raise

    with SessionLocal() as db:                      # short session #2
        image = db.get(Image, image_id)
        if image is None:                           # deleted while we worked (ADR-09)
            delete_files(paths.values())
            return
        image.thumbnails = {str(size): path.name for size, path in paths.items()}
        image.status = "done"
        db.commit()
```

## Quick reference

| Topic | Rule |
|---|---|
| Run worker | `uv run celery -A app.worker.celery_app worker --pool=solo -l info` (prefork does not work on Windows) |
| Read progress | `AsyncResult(task_id, app=celery_app)`: if `.state == "PROGRESS"`, `.info` is `{"current", "total"}` |
| Status flow | `queued → processing → done | failed`; write every transition to the DB |
| DB sessions | open → read/write → commit → close; **never** keep one open while Pillow runs (ADR-06) |
| Idempotency | a retry just overwrites the thumbnails |
| Tests | `task_always_eager=True`, `task_eager_propagates=True`, `result_backend="cache+memory://"`, all set before the first task runs |

## Common mistakes
- Passing file bytes or paths in the task arguments. Pass the `image_id` and read the path from the DB.
- Marking a lock error (`OperationalError`) as `failed` instead of letting it retry.
- Showing status from `AsyncResult` only. Results expire, so the DB must hold the status.
- Exposing `task_id` in an API response. It is internal.
- Adding Celery beat, chords, or groups. Not needed here.
