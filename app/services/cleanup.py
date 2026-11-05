from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Image, Upload, UploadStatus


def cleanup_abandoned(db: Session, now: datetime) -> dict[str, int]:
    """Remove what expired sessions and crashed uploads left behind (ADR-18)."""
    expired = Upload.expires_at < now
    has_images = exists().where(Image.upload_id == Upload.id)

    sessions_deleted = db.execute(delete(Upload).where(expired, ~has_images)).rowcount
    sessions_failed = db.execute(
        update(Upload)
        .where(expired, Upload.status == UploadStatus.uploading)
        .values(status=UploadStatus.failed)
    ).rowcount
    db.commit()

    return {
        "sessions_deleted": sessions_deleted,
        "sessions_failed": sessions_failed,
        "temp_files_deleted": delete_stale_temp_files(now),
    }


def delete_stale_temp_files(now: datetime) -> int:
    cutoff = now - timedelta(seconds=settings.upload_session_ttl_seconds)
    deleted = 0
    for path in settings.temp_dir.iterdir():
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if path.is_file() and modified_at < cutoff:
            path.unlink(missing_ok=True)
            deleted += 1
    return deleted
