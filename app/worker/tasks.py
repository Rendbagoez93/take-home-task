import logging
from pathlib import Path

from celery import Task
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db import SessionLocal
from app.models import Image, ImageStatus
from app.services.thumbnails import make_thumbnails
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# Image.error is returned by the API. Pillow's messages contain server file paths, so
# the row gets this fixed text; the re-raised exception keeps the details in the worker log.
THUMBNAIL_FAILED_MESSAGE = "Thumbnails could not be generated from this image"


@celery_app.task(
    bind=True,
    autoretry_for=(OperationalError,),  # SQLite "database is locked" (ADR-06)
    retry_backoff=True,
    max_retries=settings.thumbnail_task_max_retries,
)
def generate_thumbnails(self: Task, image_id: str) -> None:
    src = start_processing(image_id)
    if src is None:
        logger.info("Image %s or its original file is gone; nothing to do", image_id)
        return

    def report_progress(current: int, total: int) -> None:
        self.update_state(state="PROGRESS", meta={"current": current, "total": total})

    # No DB session is open while Pillow works (ADR-06).
    try:
        thumbnails = make_thumbnails(
            src, settings.thumbnail_sizes, settings.thumbnails_dir, on_progress=report_progress
        )
    except OperationalError:
        raise  # a lock on the result backend: let autoretry run instead of failing the image
    except Exception:
        mark_failed(image_id, THUMBNAIL_FAILED_MESSAGE)
        raise
    save_thumbnails(image_id, thumbnails)


def start_processing(image_id: str) -> Path | None:
    with SessionLocal() as db:
        image = db.get(Image, image_id)
        if image is None:
            return None
        src = settings.originals_dir / image.stored_filename
        if not src.exists():
            return None
        image.status = ImageStatus.processing
        db.commit()
        return src


def save_thumbnails(image_id: str, thumbnails: dict[int, Path]) -> None:
    with SessionLocal() as db:
        image = db.get(Image, image_id)
        if image is None:  # deleted while the thumbnails were being made (ADR-09)
            for path in thumbnails.values():
                path.unlink(missing_ok=True)
            return
        image.thumbnails = {str(size): path.name for size, path in thumbnails.items()}
        image.status = ImageStatus.done
        db.commit()


def mark_failed(image_id: str, error: str) -> None:
    with SessionLocal() as db:
        image = db.get(Image, image_id)
        if image is None:
            return
        image.status = ImageStatus.failed
        image.error = error
        db.commit()
