from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from python_multipart.exceptions import MultipartParseError
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, get_db
from app.models import Upload, UploadStatus, utcnow
from app.schemas import UploadCreated, UploadFilesResponse, UploadStatusResponse, error_responses
from app.services.progress import build_upload_status
from app.services.upload_service import create_upload, receive_files

# The endpoint reads the raw stream, so FastAPI cannot describe the body itself (ADR-02).
FILES_REQUEST_BODY = {
    "requestBody": {
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                            "maxItems": settings.max_files_per_request,
                        }
                    },
                }
            }
        },
        "required": True,
    }
}

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("", status_code=201, response_model=UploadCreated)
def create_upload_session(db: Annotated[Session, Depends(get_db)]) -> Upload:
    return create_upload(db)


@router.post(
    "/{upload_id}/files",
    status_code=202,
    response_model=UploadFilesResponse,
    responses=error_responses(400, 404, 409, 410, 411, 413, 422),
    openapi_extra=FILES_REQUEST_BODY,
)
async def upload_files(upload_id: str, request: Request) -> UploadFilesResponse:
    # Takes the raw Request: an UploadFile parameter would make FastAPI read the whole
    # body before this code runs, leaving no upload progress to count (ADR-02).
    content_length = request.headers.get("content-length")
    bytes_expected = await anyio.to_thread.run_sync(claim_session, upload_id, content_length)
    content_type = request.headers.get("content-type", "")
    try:
        files = await receive_files(upload_id, content_type, request.stream(), bytes_expected)
    except MultipartParseError as error:
        raise HTTPException(400, str(error)) from error
    return UploadFilesResponse(upload_id=upload_id, accepted=files.accepted, rejected=files.rejected)


@router.get("/{upload_id}", response_model=UploadStatusResponse, responses=error_responses(404, 422))
def get_upload_status(upload_id: str, db: Annotated[Session, Depends(get_db)]) -> UploadStatusResponse:
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(404, "Upload not found")
    return build_upload_status(db, upload)


def claim_session(upload_id: str, content_length: str | None) -> int:
    """Check that the session may take this file request, mark it uploading, return bytes_expected."""
    with SessionLocal() as db:
        upload = db.get(Upload, upload_id)
        if upload is None:
            raise HTTPException(404, "Upload not found")
        # Checked only here, at the start: an upload already running is not cut off (ADR-18).
        if upload.expires_at < utcnow():
            raise HTTPException(410, "Upload session has expired")
        if upload.status != UploadStatus.created:
            raise HTTPException(409, "Upload session was already used")
        bytes_expected = required_content_length(content_length)
        # Conditional on status, so of two requests racing for one session only one wins.
        claimed = db.execute(
            update(Upload)
            .where(Upload.id == upload_id, Upload.status == UploadStatus.created)
            .values(status=UploadStatus.uploading, bytes_expected=bytes_expected)
        ).rowcount
        db.commit()
    if not claimed:
        raise HTTPException(409, "Upload session was already used")
    return bytes_expected


def required_content_length(content_length: str | None) -> int:
    if content_length is None or not content_length.isdigit():
        raise HTTPException(411, "A valid Content-Length header is required")
    if int(content_length) > settings.max_request_bytes:
        raise HTTPException(413, f"Request body is larger than {settings.max_request_bytes} bytes")
    return int(content_length)
