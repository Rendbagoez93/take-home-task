import os
import subprocess
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Image, ImageStatus, Upload, utcnow
from app.worker import tasks
from app.worker.celery_app import celery_app
from app.worker.tasks import generate_thumbnails

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MakeImage = Callable[[bytes], Image]


@pytest.fixture
def make_stored_image(db: Session, settings: Settings) -> MakeImage:
    def make(content: bytes) -> Image:
        upload = Upload(expires_at=utcnow() + timedelta(minutes=10))
        db.add(upload)
        db.flush()
        image = Image(
            upload_id=upload.id,
            original_filename="cat.png",
            stored_filename="0b6f1c2e-stored.png",
            content_type="image/png",
            size_bytes=len(content),
            width=64,
            height=48,
        )
        (settings.originals_dir / image.stored_filename).write_bytes(content)
        db.add(image)
        db.commit()
        return image

    return make


@pytest.fixture
def png_content(make_image_file: Callable[..., Path]) -> bytes:
    return make_image_file("PNG", size=(600, 300)).read_bytes()


def test_generate_thumbnails_saves_thumbnails_and_marks_done(
    make_stored_image: MakeImage, png_content: bytes, db: Session, settings: Settings
) -> None:
    image = make_stored_image(png_content)

    generate_thumbnails.delay(image.id)

    db.refresh(image)
    assert image.status == ImageStatus.done
    assert set(image.thumbnails) == {"128", "256", "512"}
    for name in image.thumbnails.values():
        assert (settings.thumbnails_dir / name).is_file()


def test_generate_thumbnails_reports_progress_per_thumbnail(
    make_stored_image: MakeImage, png_content: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = make_stored_image(png_content)
    reported: list[dict[str, int]] = []
    monkeypatch.setattr(
        generate_thumbnails, "update_state", lambda state, meta: reported.append(meta), raising=False
    )

    generate_thumbnails.delay(image.id)

    assert reported == [{"current": 1, "total": 3}, {"current": 2, "total": 3}, {"current": 3, "total": 3}]


def test_generate_thumbnails_for_deleted_image_returns_without_error(db_engine: Engine) -> None:
    generate_thumbnails.delay("no-such-image")


def test_generate_thumbnails_with_missing_original_returns_without_error(
    make_stored_image: MakeImage, png_content: bytes, db: Session, settings: Settings
) -> None:
    image = make_stored_image(png_content)
    (settings.originals_dir / image.stored_filename).unlink()

    generate_thumbnails.delay(image.id)

    db.refresh(image)
    assert image.status == ImageStatus.queued


def test_generate_thumbnails_image_deleted_during_processing_removes_new_thumbnails(
    make_stored_image: MakeImage,
    png_content: bytes,
    db: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = make_stored_image(png_content)
    real_make_thumbnails = tasks.make_thumbnails

    def make_thumbnails_then_delete_row(*args: object, **kwargs: object) -> dict[int, Path]:
        thumbnails = real_make_thumbnails(*args, **kwargs)
        db.delete(image)
        db.commit()
        return thumbnails

    monkeypatch.setattr(tasks, "make_thumbnails", make_thumbnails_then_delete_row)

    generate_thumbnails.delay(image.id)

    assert list(settings.thumbnails_dir.iterdir()) == []


def test_generate_thumbnails_corrupt_file_marks_failed(make_stored_image: MakeImage, db: Session) -> None:
    image = make_stored_image(b"\x89PNG\r\n\x1a\n this is not really a png")

    with pytest.raises(OSError):
        generate_thumbnails.delay(image.id)

    db.refresh(image)
    assert image.status == ImageStatus.failed
    assert image.error == "Thumbnails could not be generated from this image"


def test_generate_thumbnails_failure_does_not_store_server_path(
    make_stored_image: MakeImage, db: Session, settings: Settings
) -> None:
    image = make_stored_image(b"\x89PNG\r\n\x1a\n this is not really a png")

    with pytest.raises(OSError, match="cannot identify image file"):
        generate_thumbnails.delay(image.id)

    db.refresh(image)
    assert image.stored_filename not in image.error
    assert str(settings.data_dir) not in image.error


def test_celery_app_tracks_progress_and_acks_late() -> None:
    conf = celery_app.conf

    assert conf.task_track_started is True
    assert conf.result_extended is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_acks_late is True
    assert conf.result_expires == 24 * 60 * 60


def test_worker_does_not_import_the_fastapi_app() -> None:
    code = "import sys, app.worker.tasks; print(any(m == 'main' or m.startswith('app.api') for m in sys.modules))"
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "False"
