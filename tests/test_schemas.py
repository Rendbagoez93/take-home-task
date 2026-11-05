from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.models import Image, ImageStatus, Upload, UploadStatus
from app.schemas import (
    FileResult,
    ImageProgress,
    ImageRead,
    ImageUpdate,
    Page,
    ProgressInfo,
    RejectedFile,
    UploadCreated,
    UploadFilesResponse,
    UploadStatusResponse,
)

EXPIRES_AT = datetime(2026, 1, 1, 12, 10, tzinfo=UTC)


def make_image(db: Session) -> Image:
    upload = Upload(expires_at=EXPIRES_AT)
    db.add(upload)
    db.commit()
    image = Image(
        upload_id=upload.id,
        original_filename="cat.png",
        stored_filename="0b6f.png",
        content_type="image/png",
        size_bytes=1234,
        width=640,
        height=480,
        task_id="celery-task-id",
        thumbnails={"128": "0b6f_128.webp", "256": "0b6f_256.webp"},
    )
    db.add(image)
    db.commit()
    return image


def test_upload_created_reads_id_from_upload_row(db: Session) -> None:
    upload = Upload(expires_at=EXPIRES_AT)
    db.add(upload)
    db.commit()

    body = UploadCreated.model_validate(upload).model_dump(mode="json")

    assert body == {
        "upload_id": upload.id,
        "status": "created",
        "expires_at": "2026-01-01T12:10:00Z",
    }


def test_image_read_exposes_thumbnail_urls_by_size(db: Session) -> None:
    image = make_image(db)

    body = ImageRead.model_validate(image).model_dump(mode="json")

    assert body["thumbnail_urls"] == {
        "128": f"/images/{image.id}/thumbnails/128",
        "256": f"/images/{image.id}/thumbnails/256",
    }


def test_image_read_hides_internal_fields(db: Session) -> None:
    image = make_image(db)

    body = ImageRead.model_validate(image).model_dump(mode="json")

    assert "task_id" not in body
    assert "stored_filename" not in body
    assert "thumbnails" not in body
    assert "0b6f" not in str(body)


def test_image_read_has_status_and_dimensions(db: Session) -> None:
    image = make_image(db)

    body = ImageRead.model_validate(image).model_dump(mode="json")

    assert body["status"] == "queued"
    assert (body["width"], body["height"], body["size_bytes"]) == (640, 480, 1234)
    assert body["original_filename"] == "cat.png"


def test_image_update_keeps_only_fields_that_were_sent() -> None:
    update = ImageUpdate.model_validate({"title": "Sunset"})

    assert update.model_dump(exclude_unset=True) == {"title": "Sunset"}


def test_image_update_accepts_null_to_clear_a_field() -> None:
    update = ImageUpdate.model_validate({"description": None})

    assert update.model_dump(exclude_unset=True) == {"description": None}


def test_image_update_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ImageUpdate.model_validate({"status": "done"})


@pytest.mark.parametrize(("field", "max_length"), [("title", 100), ("description", 1000)])
def test_image_update_rejects_text_over_max_length(field: str, max_length: int) -> None:
    ImageUpdate.model_validate({field: "x" * max_length})

    with pytest.raises(ValidationError):
        ImageUpdate.model_validate({field: "x" * (max_length + 1)})


def test_rejected_file_rejects_unknown_code() -> None:
    with pytest.raises(ValidationError):
        RejectedFile(filename="a.txt", reason="nope", code="bad_code")


def test_rejected_file_reads_upload_rejected_files_json() -> None:
    stored = {"filename": "a.txt", "reason": "not an image", "code": "unsupported_type"}

    assert RejectedFile.model_validate(stored).model_dump() == stored


@pytest.mark.parametrize(
    ("current", "total", "percent"),
    [(0, 3, 0.0), (1, 3, 33.3), (3, 3, 100.0), (512, 1024, 50.0), (0, 0, 0.0)],
)
def test_progress_info_percent_is_computed(current: int, total: int, percent: float) -> None:
    assert ProgressInfo(current=current, total=total).model_dump()["percent"] == percent


def test_progress_info_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        ProgressInfo(current=-1, total=3)


def test_upload_status_response_nests_image_progress() -> None:
    response = UploadStatusResponse(
        upload_id="u1",
        status=UploadStatus.uploading,
        upload=ProgressInfo(current=512, total=1024),
        images=[
            ImageProgress(
                image_id="i1",
                filename="cat.png",
                status=ImageStatus.processing,
                progress=ProgressInfo(current=1, total=3),
            )
        ],
        rejected=[RejectedFile(filename="big.png", reason="over 1 MB", code="too_large")],
    )

    body = response.model_dump(mode="json")

    assert body["upload"]["percent"] == 50.0
    assert body["images"][0]["progress"] == {"current": 1, "total": 3, "percent": 33.3}
    assert body["rejected"][0]["code"] == "too_large"


def test_upload_files_response_lists_accepted_and_rejected() -> None:
    response = UploadFilesResponse(
        upload_id="u1",
        accepted=[FileResult(image_id="i1", filename="cat.png")],
        rejected=[],
    )

    assert response.model_dump() == {
        "upload_id": "u1",
        "accepted": [{"image_id": "i1", "filename": "cat.png"}],
        "rejected": [],
    }


def test_page_of_image_read_holds_items_and_paging(db: Session) -> None:
    image = make_image(db)

    page = Page[ImageRead](items=[ImageRead.model_validate(image)], total=1, skip=0, limit=20)

    assert page.model_dump(mode="json")["items"][0]["id"] == image.id
    assert (page.total, page.skip, page.limit) == (1, 0, 20)


@pytest.mark.parametrize(
    "schema",
    [
        UploadCreated,
        FileResult,
        RejectedFile,
        UploadFilesResponse,
        ProgressInfo,
        ImageProgress,
        UploadStatusResponse,
        ImageRead,
        ImageUpdate,
    ],
)
def test_schema_example_matches_its_fields(schema: type[BaseModel]) -> None:
    json_schema = schema.model_json_schema(mode="serialization")
    [example] = json_schema["examples"]

    assert set(example) == set(json_schema["properties"])
