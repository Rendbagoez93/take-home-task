---
name: adding-api-endpoints
description: Use when adding or changing a FastAPI route, router, Pydantic schema, dependency, exception handler, API key auth (X-API-Key), lifespan startup, or Swagger/OpenAPI docs in the image upload service.
---

# Adding API Endpoints

## Overview
Routes are thin: HTTP in, service call, HTTP out. Business logic lives in `services/` (ADR-13). Auth is attached once per router, not per endpoint (ADR-16).

## Pattern

```python
# api/images.py
router = APIRouter(prefix="/images", tags=["images"])

@router.patch("/{image_id}", response_model=ImageRead, responses={404: {"model": ErrorResponse}})
def update_image(image_id: str, body: ImageUpdate, db: Session = Depends(get_db)) -> Image:
    image = db.get(Image, image_id)
    if image is None:
        raise HTTPException(404, "Image not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(image, field, value)
    db.commit()
    return image

# main.py
app.include_router(images.router, dependencies=[Depends(require_api_key)])
```

## Quick reference

| Topic | Rule | ADR |
|---|---|---|
| Layers | `api → services → models/storage`. Only `main.py` imports `api` | 13 |
| `async` vs `def` | `async def` **only** for `POST /uploads/{id}/files`; every other route is `def` | 12 |
| Auth | `APIKeyHeader(name="X-API-Key", auto_error=False)` + `secrets.compare_digest` against each key; failure → `401` with `WWW-Authenticate: ApiKey`; never log keys | 16 |
| Public routes | only `/health`, `/docs`, `/openapi.json`; `/health` lives outside the protected routers | 16 |
| Fail closed | empty `API_KEYS` → the app refuses to start | 16 |
| Input schemas | `model_config = ConfigDict(extra="forbid")`; length limits via `Field(max_length=...)` | PLAN F2 |
| Output schemas | `ConfigDict(from_attributes=True)`; add a `json_schema_extra` example | PLAN F2 |
| Error shape | global handler returns `{detail, code}` | PLAN F8 |
| Swagger | list non-2xx statuses in `responses={...}`; tags `uploads`, `images`, `health` | PLAN F8 |
| Status codes | `201` create session, `202` files accepted, `204` delete; others per PLAN §6 | — |
| Lifespan | create folders → `create_all` → `cleanup_abandoned(now=utcnow())` → log summary | 18 |
| Downloads | `FileResponse` with the **detected** content type | 10 |

## Common mistakes
- Putting validation, file I/O, or Celery calls straight into a route body. Move them to a service.
- Adding `Depends(require_api_key)` per route, or adding a new router without it. The auth sweep test in `test_security.py` must stay green.
- Using `async def` for a CRUD route that calls sync SQLAlchemy. It blocks the event loop.
- Returning `task_id`, `stored_filename`, or filesystem paths in responses.
- Writing a custom pagination or response-envelope framework. `Page[ImageRead]` with `items`, `total`, `skip`, `limit` is enough.
