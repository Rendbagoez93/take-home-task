from celery.result import AsyncResult
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Image, ImageStatus, Upload
from app.schemas import ImageProgress, ProgressInfo, RejectedFile, UploadStatusResponse
from app.worker.celery_app import celery_app


def build_upload_status(db: Session, upload: Upload) -> UploadStatusResponse:
    images = db.scalars(
        select(Image).where(Image.upload_id == upload.id).order_by(Image.created_at)
    ).all()
    return UploadStatusResponse(
        upload_id=upload.id,
        status=upload.status,
        upload=ProgressInfo(current=upload.bytes_received, total=upload.bytes_expected or 0),
        images=[
            ImageProgress(
                image_id=image.id,
                filename=image.original_filename,
                status=image.status,
                progress=thumbnail_progress(image),
            )
            for image in images
        ],
        rejected=[RejectedFile.model_validate(file) for file in upload.rejected_files],
    )


def thumbnail_progress(image: Image) -> ProgressInfo:
    total = len(settings.thumbnail_sizes)
    if image.status == ImageStatus.done:
        return ProgressInfo(current=total, total=total)
    if image.status != ImageStatus.processing or image.task_id is None:
        return ProgressInfo(current=0, total=total)
    # The DB status is the source of truth; Celery is asked only how far a running
    # task has got, because its results expire (ADR-07).
    result = AsyncResult(image.task_id, app=celery_app)
    if result.state != "PROGRESS":
        return ProgressInfo(current=0, total=total)
    return ProgressInfo(current=result.info["current"], total=result.info["total"])
