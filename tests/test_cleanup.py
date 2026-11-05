import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Image, Upload, UploadStatus
from app.services.cleanup import cleanup_abandoned

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
EXPIRED = NOW - timedelta(seconds=1)
NOT_EXPIRED = NOW + timedelta(minutes=5)


def add_upload(db: Session, expires_at: datetime, status: UploadStatus = UploadStatus.created) -> str:
    upload = Upload(expires_at=expires_at, status=status)
    db.add(upload)
    db.commit()
    return upload.id


def add_image(db: Session, upload_id: str) -> None:
    db.add(
        Image(
            upload_id=upload_id,
            original_filename="cat.png",
            stored_filename="0b6f.png",
            content_type="image/png",
            size_bytes=1234,
            width=64,
            height=48,
        )
    )
    db.commit()


def write_temp_file(settings: Settings, name: str, age: timedelta) -> Path:
    path = settings.temp_dir / name
    path.write_bytes(b"partial upload")
    modified_at = (NOW - age).timestamp()
    os.utime(path, (modified_at, modified_at))
    return path


def test_cleanup_deletes_expired_session_without_images(db: Session) -> None:
    upload_id = add_upload(db, EXPIRED, UploadStatus.completed)

    summary = cleanup_abandoned(db, now=NOW)

    assert db.get(Upload, upload_id) is None
    assert summary["sessions_deleted"] == 1


def test_cleanup_keeps_expired_session_with_images(db: Session) -> None:
    upload_id = add_upload(db, EXPIRED, UploadStatus.completed)
    add_image(db, upload_id)

    summary = cleanup_abandoned(db, now=NOW)

    assert db.get(Upload, upload_id) is not None
    assert summary["sessions_deleted"] == 0


def test_cleanup_leaves_unexpired_session_alone(db: Session) -> None:
    upload_id = add_upload(db, NOT_EXPIRED, UploadStatus.uploading)

    summary = cleanup_abandoned(db, now=NOW)

    upload = db.get(Upload, upload_id)
    assert upload is not None
    assert upload.status == UploadStatus.uploading
    assert summary == {"sessions_deleted": 0, "sessions_failed": 0, "temp_files_deleted": 0}


def test_cleanup_marks_stale_uploading_session_failed_and_keeps_images(db: Session) -> None:
    upload_id = add_upload(db, EXPIRED, UploadStatus.uploading)
    add_image(db, upload_id)

    summary = cleanup_abandoned(db, now=NOW)

    db.expire_all()
    assert db.get(Upload, upload_id).status == UploadStatus.failed
    assert len(db.scalars(select(Image).where(Image.upload_id == upload_id)).all()) == 1
    assert summary["sessions_failed"] == 1


def test_cleanup_deletes_old_temp_files_only(db: Session, settings: Settings) -> None:
    ttl = timedelta(seconds=settings.upload_session_ttl_seconds)
    old_file = write_temp_file(settings, "old.tmp", age=ttl + timedelta(seconds=1))
    new_file = write_temp_file(settings, "new.tmp", age=timedelta(seconds=30))

    summary = cleanup_abandoned(db, now=NOW)

    assert not old_file.exists()
    assert new_file.exists()
    assert summary["temp_files_deleted"] == 1
