from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import Literal
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Dialect, ForeignKey, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    """Stores datetimes as UTC and reads them back timezone-aware."""

    # SQLite has no timezone type and drops tzinfo, which would make expiry comparisons
    # against utcnow() fail with naive/aware TypeErrors.
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: pass a timezone-aware value")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class UploadStatus(StrEnum):
    created = auto()
    uploading = auto()
    completed = auto()
    failed = auto()


class ImageStatus(StrEnum):
    queued = auto()
    processing = auto()
    done = auto()
    failed = auto()


RejectionCode = Literal["too_large", "unsupported_type", "invalid_image", "too_many_files", "empty_file"]


class Base(DeclarativeBase):
    pass


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    status: Mapped[UploadStatus] = mapped_column(default=UploadStatus.created)
    bytes_expected: Mapped[int | None]
    bytes_received: Mapped[int] = mapped_column(default=0)
    rejected_files: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Image(Base):
    __tablename__ = "images"

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id"), index=True)
    original_filename: Mapped[str]
    stored_filename: Mapped[str]
    content_type: Mapped[str]
    size_bytes: Mapped[int]
    width: Mapped[int]
    height: Mapped[int]
    title: Mapped[str | None]
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ImageStatus] = mapped_column(default=ImageStatus.queued)
    error: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[str | None]
    thumbnails: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
