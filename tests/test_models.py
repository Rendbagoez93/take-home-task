from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Engine, inspect, select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from app.models import Image, ImageStatus, Upload, UploadStatus

EXPIRES_AT = datetime(2026, 1, 1, 12, 10, tzinfo=UTC)


def make_upload(db: Session) -> Upload:
    upload = Upload(expires_at=EXPIRES_AT)
    db.add(upload)
    db.commit()
    return upload


def make_image(db: Session, upload: Upload) -> Image:
    image = Image(
        upload_id=upload.id,
        original_filename="cat.png",
        stored_filename="0b6f.png",
        content_type="image/png",
        size_bytes=1234,
        width=640,
        height=480,
    )
    db.add(image)
    db.commit()
    return image


def test_upload_is_created_with_defaults(db_engine: Engine, db: Session) -> None:
    upload_id = make_upload(db).id

    with Session(db_engine) as fresh:
        upload = fresh.get(Upload, upload_id)
        assert upload.status == UploadStatus.created
        assert upload.bytes_received == 0
        assert upload.bytes_expected is None
        assert upload.rejected_files == []
        assert upload.created_at is not None


def test_upload_rejected_files_round_trip_as_json(db_engine: Engine, db: Session) -> None:
    upload = make_upload(db)
    rejected = [{"filename": "a.txt", "reason": "not an image", "code": "unsupported_type"}]
    upload.rejected_files = rejected
    db.commit()

    with Session(db_engine) as fresh:
        assert fresh.get(Upload, upload.id).rejected_files == rejected


def test_upload_times_are_read_back_as_utc_aware(db_engine: Engine, db: Session) -> None:
    upload_id = make_upload(db).id

    with Session(db_engine) as fresh:
        upload = fresh.get(Upload, upload_id)
        assert upload.expires_at == EXPIRES_AT
        assert upload.expires_at.tzinfo == UTC
        assert upload.created_at.tzinfo == UTC


def test_upload_non_utc_time_is_stored_as_utc(db_engine: Engine, db: Session) -> None:
    jakarta = timezone(timedelta(hours=7))
    upload = Upload(expires_at=EXPIRES_AT.astimezone(jakarta))
    db.add(upload)
    db.commit()

    with Session(db_engine) as fresh:
        assert fresh.get(Upload, upload.id).expires_at == EXPIRES_AT


def test_upload_naive_time_is_rejected(db: Session) -> None:
    db.add(Upload(expires_at=datetime(2026, 1, 1, 12, 10)))

    with pytest.raises(StatementError):
        db.commit()


def test_upload_updated_at_changes_on_update(db: Session) -> None:
    upload = make_upload(db)
    first_updated_at = upload.updated_at

    upload.status = UploadStatus.uploading
    db.commit()

    assert upload.updated_at > first_updated_at


def test_upload_expires_at_is_indexed(db_engine: Engine) -> None:
    indexed_columns = [index["column_names"] for index in inspect(db_engine).get_indexes("uploads")]

    assert ["expires_at"] in indexed_columns


def test_image_is_created_with_defaults(db_engine: Engine, db: Session) -> None:
    image_id = make_image(db, make_upload(db)).id

    with Session(db_engine) as fresh:
        image = fresh.get(Image, image_id)
        assert image.status == ImageStatus.queued
        assert image.thumbnails == {}
        assert image.title is None
        assert image.description is None
        assert image.error is None
        assert image.task_id is None
        assert image.created_at.tzinfo == UTC


def test_image_can_be_listed_by_upload(db: Session) -> None:
    upload = make_upload(db)
    image = make_image(db, upload)
    make_image(db, make_upload(db))

    found = db.scalars(select(Image).where(Image.upload_id == upload.id)).all()

    assert [found_image.id for found_image in found] == [image.id]
