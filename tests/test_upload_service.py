from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from PIL import Image as PILImage
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from app.config import Settings
from app.models import Upload, UploadStatus, utcnow
from app.services.upload_service import receive_files, should_write_progress

THREE_MB = 3 * 1024 * 1024
BOUNDARY = "serviceboundary"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"
CHUNK_BYTES = 16 * 1024


def single_file_body(filename: str, content: bytes) -> bytes:
    headers = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        "Content-Type: image/png\r\n\r\n"
    )
    return headers.encode() + content + f"\r\n--{BOUNDARY}--\r\n".encode()


@pytest.fixture
def noise_png(tmp_path: Path) -> bytes:
    path = tmp_path / "noise.png"
    PILImage.effect_noise((512, 512), 64).save(path, "PNG")  # noise does not compress: ~260 KB
    return path.read_bytes()


@pytest.fixture
def uploading_session(db: Session) -> Upload:
    upload = Upload(status=UploadStatus.uploading, expires_at=utcnow() + timedelta(minutes=10))
    db.add(upload)
    db.commit()
    return upload


def test_should_write_progress_after_min_bytes(settings: Settings) -> None:
    received = settings.progress_write_min_bytes

    assert should_write_progress(received, last_written=0, expected=THREE_MB) is True


def test_should_write_progress_after_min_fraction_of_small_request(settings: Settings) -> None:
    expected = 100_000

    assert should_write_progress(5_000, last_written=0, expected=expected) is True


def test_should_write_progress_skips_small_growth(settings: Settings) -> None:
    last_written = 200_000

    assert should_write_progress(last_written + 1_000, last_written, expected=THREE_MB) is False


def test_receive_files_writes_bytes_received_while_streaming(
    uploading_session: Upload, noise_png: bytes, db: Session
) -> None:
    body = single_file_body("noise.png", noise_png)
    seen_in_db: list[int] = []

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(body), CHUNK_BYTES):
            db.expire_all()
            seen_in_db.append(db.get_one(Upload, uploading_session.id).bytes_received)
            yield body[start : start + CHUNK_BYTES]

    anyio.run(receive_files, uploading_session.id, CONTENT_TYPE, chunks(), len(body))

    assert any(0 < received < len(body) for received in seen_in_db)
    db.expire_all()
    assert db.get_one(Upload, uploading_session.id).bytes_received == len(body)


def test_receive_files_client_disconnect_fails_upload_and_deletes_temp_files(
    uploading_session: Upload, noise_png: bytes, db: Session, settings: Settings
) -> None:
    body = single_file_body("noise.png", noise_png)

    async def disconnecting_chunks() -> AsyncIterator[bytes]:
        yield body[: len(body) // 2]
        raise ClientDisconnect()

    with pytest.raises(ClientDisconnect):
        anyio.run(receive_files, uploading_session.id, CONTENT_TYPE, disconnecting_chunks(), len(body))

    db.expire_all()
    assert db.get_one(Upload, uploading_session.id).status == UploadStatus.failed
    assert list(settings.temp_dir.iterdir()) == []
