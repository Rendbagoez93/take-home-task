import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import images, uploads
from app.db import SessionLocal
from app.models import Base, utcnow
from app.schemas import error_responses
from app.security import require_api_key
from app.services.cleanup import cleanup_abandoned
from app.services.storage import create_storage_dirs

logger = logging.getLogger(__name__)


def prepare_storage() -> dict[str, int]:
    create_storage_dirs()
    with SessionLocal() as db:
        Base.metadata.create_all(db.get_bind())
        return cleanup_abandoned(db, now=utcnow())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # uvicorn configures only its own loggers; without a root handler the app's INFO
    # lines, such as the cleanup summary (ADR-18), are dropped. No-op if one exists.
    logging.basicConfig(level=logging.INFO)
    summary = await anyio.to_thread.run_sync(prepare_storage)
    logger.info("Startup cleanup: %s", summary)
    yield


def health() -> dict[str, str]:
    return {"status": "ok"}


def error_code(status_code: int) -> str:
    if status_code == 422:
        return "validation_error"
    return HTTPStatus(status_code).phrase.lower().replace(" ", "_")  # 404 -> "not_found"


def handle_http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        {"detail": error.detail, "code": error_code(error.status_code)},
        status_code=error.status_code,
        headers=error.headers,  # keeps WWW-Authenticate on 401 (ADR-16)
    )


def handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        {"detail": jsonable_encoder(error.errors()), "code": error_code(422)}, status_code=422
    )


def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    # The message can hold paths or SQL, so it goes to the log, not to the client.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error", "code": error_code(500)}, status_code=500)


def create_app() -> FastAPI:
    # ReDoc is off: the public routes are only /health, /docs and /openapi.json (ADR-16).
    app = FastAPI(title="Image Upload Service", lifespan=lifespan, redoc_url=None)
    # Auth is attached per router, not per route, so a new route cannot miss it (ADR-16).
    for router in (uploads.router, images.router):
        app.include_router(router, dependencies=[Depends(require_api_key)], responses=error_responses(401))
    app.add_api_route("/health", health, methods=["GET"], tags=["health"])
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
    return app


app = create_app()
