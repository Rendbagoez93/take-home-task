from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import Image, ImageStatus, Upload, UploadStatus, utcnow
from app.services.progress import build_upload_status
from app.worker.celery_app import celery_app


def add_upload(db: Session, **columns: object) -> Upload:
    upload = Upload(expires_at=utcnow() + timedelta(minutes=10), **columns)
    db.add(upload)
    db.commit()
    return upload


def add_image(db: Session, upload: Upload, **columns: object) -> Image:
    image = Image(
        upload_id=upload.id,
        original_filename="cat.png",
        stored_filename="stored.png",
        content_type="image/png",
        size_bytes=100,
        width=10,
        height=10,
        **columns,
    )
    db.add(image)
    db.commit()
    return image


def test_build_upload_status_reports_bytes_received_percent(db: Session) -> None:
    upload = add_upload(db, status=UploadStatus.uploading, bytes_expected=1000, bytes_received=250)

    status = build_upload_status(db, upload)

    assert status.upload.percent == 25.0


def test_build_upload_status_reads_celery_progress_for_processing_image(db: Session) -> None:
    upload = add_upload(db)
    add_image(db, upload, status=ImageStatus.processing, task_id="task-in-progress")
    celery_app.backend.store_result("task-in-progress", {"current": 2, "total": 3}, "PROGRESS")

    status = build_upload_status(db, upload)

    assert status.images[0].progress.current == 2
    assert status.images[0].progress.total == 3


def test_build_upload_status_done_image_reports_full_progress(db: Session) -> None:
    upload = add_upload(db)
    add_image(db, upload, status=ImageStatus.done)

    status = build_upload_status(db, upload)

    assert status.images[0].progress.percent == 100.0


def test_build_upload_status_queued_image_reports_zero_progress(db: Session) -> None:
    upload = add_upload(db)
    add_image(db, upload, status=ImageStatus.queued)

    status = build_upload_status(db, upload)

    assert status.images[0].progress.current == 0


def test_build_upload_status_lists_rejected_files(db: Session) -> None:
    rejected = {"filename": "notes.txt", "reason": "Extension .txt is not allowed", "code": "unsupported_type"}
    upload = add_upload(db, rejected_files=[rejected])

    status = build_upload_status(db, upload)

    assert [file.model_dump() for file in status.rejected] == [rejected]
