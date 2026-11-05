from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models import ImageStatus, RejectionCode, UploadStatus

EXAMPLE_UPLOAD_ID = "3f2b6c1e-8d4a-4b7e-9c2f-1a5d7e9b0c41"
EXAMPLE_IMAGE_ID = "9a1c4e7b-2f3d-4c8a-b6e5-0d7f1a2b3c4d"
EXAMPLE_REJECTED = {"filename": "notes.txt", "reason": "Extension .txt is not allowed", "code": "unsupported_type"}


class UploadCreated(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {"upload_id": EXAMPLE_UPLOAD_ID, "status": "created", "expires_at": "2026-01-01T12:10:00Z"}
            ]
        },
    )

    upload_id: str = Field(validation_alias="id")
    status: UploadStatus
    expires_at: datetime


class FileResult(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"image_id": EXAMPLE_IMAGE_ID, "filename": "cat.png"}]}
    )

    image_id: str
    filename: str


class RejectedFile(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [EXAMPLE_REJECTED]})

    filename: str
    reason: str
    code: RejectionCode


class UploadFilesResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "upload_id": EXAMPLE_UPLOAD_ID,
                    "accepted": [{"image_id": EXAMPLE_IMAGE_ID, "filename": "cat.png"}],
                    "rejected": [EXAMPLE_REJECTED],
                }
            ]
        }
    )

    upload_id: str
    accepted: list[FileResult]
    rejected: list[RejectedFile]


class ProgressInfo(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"current": 1, "total": 3, "percent": 33.3}]}
    )

    current: int = Field(ge=0)
    total: int = Field(ge=0)

    @computed_field
    @property
    def percent(self) -> float:
        if self.total == 0:  # bytes_expected is unknown until the file request arrives
            return 0.0
        return round(100 * self.current / self.total, 1)


class ImageProgress(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "image_id": EXAMPLE_IMAGE_ID,
                    "filename": "cat.png",
                    "status": "processing",
                    "progress": {"current": 1, "total": 3, "percent": 33.3},
                }
            ]
        }
    )

    image_id: str
    filename: str
    status: ImageStatus
    progress: ProgressInfo


class UploadStatusResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "upload_id": EXAMPLE_UPLOAD_ID,
                    "status": "completed",
                    "upload": {"current": 1048576, "total": 1048576, "percent": 100.0},
                    "images": [
                        {
                            "image_id": EXAMPLE_IMAGE_ID,
                            "filename": "cat.png",
                            "status": "processing",
                            "progress": {"current": 1, "total": 3, "percent": 33.3},
                        }
                    ],
                    "rejected": [EXAMPLE_REJECTED],
                }
            ]
        }
    )

    upload_id: str
    status: UploadStatus
    upload: ProgressInfo
    images: list[ImageProgress]
    rejected: list[RejectedFile]


class ImageRead(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": EXAMPLE_IMAGE_ID,
                    "upload_id": EXAMPLE_UPLOAD_ID,
                    "original_filename": "cat.png",
                    "content_type": "image/png",
                    "size_bytes": 482133,
                    "width": 1024,
                    "height": 768,
                    "title": "Sleepy cat",
                    "description": "Cat on the sofa",
                    "status": "done",
                    "error": None,
                    "thumbnail_urls": {
                        "128": f"/images/{EXAMPLE_IMAGE_ID}/thumbnails/128",
                        "256": f"/images/{EXAMPLE_IMAGE_ID}/thumbnails/256",
                        "512": f"/images/{EXAMPLE_IMAGE_ID}/thumbnails/512",
                    },
                    "created_at": "2026-01-01T12:00:00Z",
                    "updated_at": "2026-01-01T12:00:03Z",
                }
            ]
        },
    )

    id: str
    upload_id: str
    original_filename: str
    content_type: str
    size_bytes: int
    width: int
    height: int
    title: str | None
    description: str | None
    status: ImageStatus
    error: str | None
    thumbnails: dict[str, str] = Field(exclude=True)  # values are stored filenames; never sent to clients
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def thumbnail_urls(self) -> dict[str, str]:
        return {size: f"/images/{self.id}/thumbnails/{size}" for size in self.thumbnails}


class ImageUpdate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"title": "Sleepy cat", "description": "Cat on the sofa"}]},
    )

    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=1000)


class Page[ItemT](BaseModel):
    items: list[ItemT]
    total: int
    skip: int
    limit: int


class ErrorResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"detail": "Image not found", "code": "not_found"}]}
    )

    # A message for HTTP errors; the list of field errors for request validation (422).
    detail: str | list[dict[str, Any]]
    code: str


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Swagger `responses=` entries that document these statuses with the ErrorResponse body."""
    return {status_code: {"model": ErrorResponse} for status_code in status_codes}
