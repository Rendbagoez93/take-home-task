from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Image, ImageStatus
from app.schemas import ImageRead, ImageUpdate, Page, error_responses
from app.services.storage import delete_image_files

router = APIRouter(prefix="/images", tags=["images"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=Page[ImageRead], responses=error_responses(422))
def list_images(
    db: DbSession,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=settings.max_page_size)] = settings.default_page_size,
    upload_id: str | None = None,
    status: ImageStatus | None = None,
) -> Page[ImageRead]:
    query = select(Image)
    if upload_id is not None:
        query = query.where(Image.upload_id == upload_id)
    if status is not None:
        query = query.where(Image.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    images = db.scalars(query.order_by(Image.created_at, Image.id).offset(skip).limit(limit)).all()
    return Page[ImageRead](
        items=[ImageRead.model_validate(image) for image in images], total=total, skip=skip, limit=limit
    )


@router.get("/{image_id}", response_model=ImageRead, responses=error_responses(404, 422))
def get_image(image_id: str, db: DbSession) -> Image:
    return find_image(db, image_id)


@router.get("/{image_id}/file", response_class=FileResponse, responses=error_responses(404, 422))
def download_file(image_id: str, db: DbSession) -> FileResponse:
    image = find_image(db, image_id)
    # The type Pillow detected at upload, not the one the client claimed (ADR-10).
    return FileResponse(settings.originals_dir / image.stored_filename, media_type=image.content_type)


@router.get(
    "/{image_id}/thumbnails/{size}", response_class=FileResponse, responses=error_responses(404, 422)
)
def download_thumbnail(image_id: str, size: int, db: DbSession) -> FileResponse:
    if size not in settings.thumbnail_sizes:
        raise HTTPException(422, f"Thumbnail size must be one of {settings.thumbnail_sizes}")
    image = find_image(db, image_id)
    thumbnail_filename = image.thumbnails.get(str(size))
    if thumbnail_filename is None:
        raise HTTPException(404, "Thumbnail is not ready yet")
    return FileResponse(settings.thumbnails_dir / thumbnail_filename, media_type="image/webp")


@router.patch("/{image_id}", response_model=ImageRead, responses=error_responses(404, 422))
def update_image(image_id: str, body: ImageUpdate, db: DbSession) -> Image:
    image = find_image(db, image_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(image, field, value)
    db.commit()
    return image


@router.delete("/{image_id}", status_code=204, responses=error_responses(404, 422))
def delete_image(image_id: str, db: DbSession) -> Response:
    image = find_image(db, image_id)
    db.delete(image)
    # Row first, files second: a worker that finishes after this sees no row and removes
    # its own new thumbnails (ADR-09).
    db.commit()
    delete_image_files(image)
    return Response(status_code=204)


def find_image(db: Session, image_id: str) -> Image:
    image = db.get(Image, image_id)
    if image is None:
        raise HTTPException(404, "Image not found")
    return image
