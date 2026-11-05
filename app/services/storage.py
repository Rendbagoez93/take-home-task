import os
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.models import Image


def create_storage_dirs() -> None:
    for directory in (settings.originals_dir, settings.thumbnails_dir, settings.temp_dir):
        directory.mkdir(parents=True, exist_ok=True)


def new_temp_path() -> Path:
    return settings.temp_dir / f"{uuid4()}.tmp"


def promote(temp_path: Path, ext: str) -> str:
    stored_name = f"{uuid4()}.{ext}"
    os.replace(temp_path, settings.originals_dir / stored_name)  # atomic: no half-written finals (ADR-09)
    return stored_name


def delete_image_files(image: Image) -> None:
    (settings.originals_dir / image.stored_filename).unlink(missing_ok=True)
    # Glob instead of reading image.thumbnails: a task that failed midway leaves files
    # that were never recorded on the row.
    stem = Path(image.stored_filename).stem
    for thumbnail in settings.thumbnails_dir.glob(f"{stem}_*.webp"):
        thumbnail.unlink(missing_ok=True)
