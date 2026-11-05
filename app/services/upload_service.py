from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import anyio
from PIL import Image as PILImage
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Image, RejectionCode, Upload, UploadStatus, utcnow
from app.schemas import FileResult, RejectedFile
from app.services.multipart_stream import MultipartStreamHandler, PartData, PartEnd, PartEvent, PartRejected
from app.services.sanitize import strip_metadata
from app.services.storage import new_temp_path, promote
from app.services.validation import InvalidImageError, verify_image
from app.worker.tasks import generate_thumbnails


def create_upload(db: Session) -> Upload:
    upload = Upload(expires_at=utcnow() + timedelta(seconds=settings.upload_session_ttl_seconds))
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload


def should_write_progress(received: int, last_written: int, expected: int) -> bool:
    grown = received - last_written
    return (
        grown >= settings.progress_write_min_bytes
        or grown >= settings.progress_write_min_fraction * expected
    )


@dataclass
class ReceivedFiles:
    """The temp files of one file request, and the outcome of each finished part."""

    upload_id: str
    temp_paths: dict[int, Path] = field(default_factory=dict)
    accepted: list[FileResult] = field(default_factory=list)
    rejected: list[RejectedFile] = field(default_factory=list)

    def apply(self, events: list[PartEvent]) -> None:
        for event in events:
            match event:
                case PartData():
                    self.write(event)
                case PartRejected():
                    self.reject(event.index, event.filename, event.code, event.reason)
                case PartEnd():
                    self.accept(event)

    def write(self, event: PartData) -> None:
        if event.index not in self.temp_paths:
            self.temp_paths[event.index] = new_temp_path()
        with self.temp_paths[event.index].open("ab") as temp_file:
            temp_file.write(event.data)

    def reject(self, index: int, filename: str, code: RejectionCode, reason: str) -> None:
        temp_path = self.temp_paths.pop(index, None)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        self.rejected.append(RejectedFile(filename=filename, reason=reason, code=code))

    def accept(self, event: PartEnd) -> None:
        temp_path = self.temp_paths[event.index]
        try:
            image_format, _, _ = verify_image(temp_path)
            if image_format != event.image_format:
                claimed = event.image_format
                raise InvalidImageError(f"File content is {image_format}, not the {claimed} its extension claims")
            width, height, size_bytes = strip_metadata(temp_path, image_format)
        except InvalidImageError as error:
            self.reject(event.index, event.filename, "invalid_image", str(error))
            return

        stored_filename = promote(temp_path, image_format.lower())
        del self.temp_paths[event.index]
        image = Image(
            upload_id=self.upload_id,
            original_filename=event.filename,
            stored_filename=stored_filename,
            content_type=PILImage.MIME[image_format],  # detected by Pillow, not the client header (ADR-10)
            size_bytes=size_bytes,
            width=width,
            height=height,
        )
        image_id = commit_and_enqueue(image)
        self.accepted.append(FileResult(image_id=image_id, filename=event.filename))

    def discard_temp_files(self) -> None:
        for temp_path in self.temp_paths.values():
            temp_path.unlink(missing_ok=True)
        self.temp_paths.clear()


def commit_and_enqueue(image: Image) -> str:
    with SessionLocal() as db:
        db.add(image)
        db.flush()
        image_id = image.id
        db.commit()  # before delay(): the worker can start before an uncommitted row exists (ADR-09)
    task = generate_thumbnails.delay(image_id)
    with SessionLocal() as db:
        db.execute(update(Image).where(Image.id == image_id).values(task_id=task.id))
        db.commit()
    return image_id


async def receive_files(
    upload_id: str, content_type: str, chunks: AsyncIterator[bytes], bytes_expected: int
) -> ReceivedFiles:
    files = ReceivedFiles(upload_id)
    received = last_written = 0
    succeeded = False
    try:
        handler = MultipartStreamHandler(content_type)
        async for chunk in chunks:
            # Parsing stays on the event loop (in memory, cheap); disk, Pillow and DB
            # work goes to a thread so other requests keep running (ADR-12).
            await anyio.to_thread.run_sync(files.apply, handler.feed(chunk))
            received += len(chunk)
            if should_write_progress(received, last_written, bytes_expected):
                await anyio.to_thread.run_sync(save_bytes_received, upload_id, received)
                last_written = received
        await anyio.to_thread.run_sync(files.apply, handler.finish())
        await anyio.to_thread.run_sync(complete_upload, upload_id, received, files.rejected)
        succeeded = True
    finally:
        # Shielded so the cleanup still runs when a disconnect cancels the request (ADR-09).
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(files.discard_temp_files)
            if not succeeded:
                await anyio.to_thread.run_sync(fail_upload, upload_id)
    return files


def save_bytes_received(upload_id: str, received: int) -> None:
    with SessionLocal() as db:
        db.execute(update(Upload).where(Upload.id == upload_id).values(bytes_received=received))
        db.commit()


def complete_upload(upload_id: str, received: int, rejected: list[RejectedFile]) -> None:
    with SessionLocal() as db:
        db.execute(
            update(Upload)
            .where(Upload.id == upload_id)
            .values(
                status=UploadStatus.completed,
                bytes_received=received,
                rejected_files=[file.model_dump() for file in rejected],
            )
        )
        db.commit()


def fail_upload(upload_id: str) -> None:
    with SessionLocal() as db:
        db.execute(update(Upload).where(Upload.id == upload_id).values(status=UploadStatus.failed))
        db.commit()
