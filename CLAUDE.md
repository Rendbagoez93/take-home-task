# Image Upload Service

FastAPI service. A client opens an upload session, streams up to 3 images (max 1 MB each) per request, and polls one endpoint for upload and processing progress. A Celery worker makes 3 WebP thumbnails per image. The service also has image CRUD, API-key auth, and EXIF/GPS stripping.

**Stack:** FastAPI, Pydantic v2, SQLAlchemy 2.x, Celery with an SQLite broker and result backend, Pillow, python-multipart.
**Environment:** Windows 11, Python 3.14, `uv`, no Docker, no Redis.

## Sources of truth

- [docs/PLAN.md](docs/PLAN.md): scope, data model, API, phases. Build what it says, nothing more.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): ADR-01 to ADR-19. These decisions are final. Follow them; don't re-debate them.
- The docs are in Indonesian. Code, comments, and commits are in English.

## Rules (always apply)

@rules.md

## Skills: load the matching one before you start

| When you are… | Skill |
|---|---|
| Starting or continuing any PLAN phase (Fase 0-8) | `implementing-a-plan-phase` |
| Working on the multipart stream, the upload endpoint, or upload progress | `building-the-upload-stream` |
| Working on the Celery app, the thumbnail task, or the SQLite broker; Windows worker issues | `writing-celery-tasks` |
| Working on validation, metadata stripping, thumbnails, storage, or cleanup | `handling-image-files` |
| Working on routes, schemas, auth, the error handler, Swagger, or lifespan | `adding-api-endpoints` |
| Writing or fixing tests and fixtures | `testing-the-service` |

General process skills that also apply: `superpowers:test-driven-development` (all new code), `superpowers:systematic-debugging` (any failure), `superpowers:verification-before-completion` (before saying "done").

## Layout (PLAN §4)

```
main.py      app factory, routers, exception handlers, lifespan (at the repo root)
app/
  config.py security.py db.py models.py schemas.py
  api/       uploads.py images.py           # HTTP only
  services/  validation sanitize storage cleanup multipart_stream
             upload_service progress thumbnails   # logic, pure where possible
  worker/    celery_app.py tasks.py          # must never import the FastAPI app
tests/       one file per service and router, plus conftest.py
data/        runtime DBs and files (gitignored)
```

Dependency direction: `api → services → models/storage`; `worker → services`.

## Commands

```bash
uv sync                                                            # install
uv run pytest                                                      # tests (must be green to finish a phase)
uv run fastapi dev main.py                                         # API at http://127.0.0.1:8000/docs
uv run celery -A app.worker.celery_app worker --pool=solo -l info  # worker (solo is required on Windows)
```

Copy `.env.example` to `.env` and set `API_KEYS` before starting the API. Never commit `.env`.

## Working agreement

- Work one PLAN phase at a time. Stop at the phase boundary and report.
- Ask before adding a dependency or deviating from an ADR.
- Commit only when the user asks.
