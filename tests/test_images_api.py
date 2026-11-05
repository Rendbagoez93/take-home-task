from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Image, ImageStatus, Upload, utcnow


@pytest.fixture
def upload_image(client: TestClient, make_image_file: Callable[..., Path]) -> Callable[..., str]:
    """Upload one PNG through the real endpoint (eager Celery) and return its image_id."""

    def upload(filename: str = "cat.png", size: tuple[int, int] = (64, 48)) -> str:
        upload_id = client.post("/uploads").json()["upload_id"]
        data = make_image_file("PNG", name=filename, size=size).read_bytes()
        response = client.post(f"/uploads/{upload_id}/files", files=[("files", (filename, data, "image/png"))])
        return response.json()["accepted"][0]["image_id"]

    return upload


@pytest.fixture
def queued_image(db: Session, make_image_file: Callable[..., Path], settings: Settings) -> Image:
    """An image whose thumbnails have not been made yet."""
    upload = Upload(expires_at=utcnow() + timedelta(minutes=10))
    db.add(upload)
    db.flush()
    stored_filename = "queued.png"
    make_image_file("PNG").replace(settings.originals_dir / stored_filename)
    image = Image(
        upload_id=upload.id,
        original_filename="queued.png",
        stored_filename=stored_filename,
        content_type="image/png",
        size_bytes=100,
        width=64,
        height=48,
    )
    db.add(image)
    db.commit()
    return image


def test_list_images_returns_page_with_total(client: TestClient, upload_image: Callable[..., str]) -> None:
    first, second = upload_image("a.png"), upload_image("b.png")

    response = client.get("/images")

    body = response.json()
    assert response.status_code == 200
    assert [item["id"] for item in body["items"]] == [first, second]
    assert body["total"] == 2
    assert body["skip"] == 0


def test_list_images_applies_skip_and_limit(client: TestClient, upload_image: Callable[..., str]) -> None:
    ids = [upload_image(f"{name}.png") for name in "abc"]

    body = client.get("/images", params={"skip": 1, "limit": 1}).json()

    assert [item["id"] for item in body["items"]] == [ids[1]]
    assert body["total"] == 3
    assert (body["skip"], body["limit"]) == (1, 1)


def test_list_images_filters_by_upload_id(
    client: TestClient, upload_image: Callable[..., str], queued_image: Image
) -> None:
    upload_image()

    body = client.get("/images", params={"upload_id": queued_image.upload_id}).json()

    assert [item["id"] for item in body["items"]] == [queued_image.id]
    assert body["total"] == 1


def test_list_images_filters_by_status(
    client: TestClient, upload_image: Callable[..., str], queued_image: Image
) -> None:
    done_id = upload_image()

    body = client.get("/images", params={"status": "done"}).json()

    assert [item["id"] for item in body["items"]] == [done_id]


@pytest.mark.parametrize("params", [{"limit": 101}, {"limit": 0}, {"skip": -1}, {"status": "unknown"}])
def test_list_images_with_invalid_query_returns_422(client: TestClient, params: dict[str, object]) -> None:
    assert client.get("/images", params=params).status_code == 422


def test_get_image_returns_metadata_and_thumbnail_urls(
    client: TestClient, upload_image: Callable[..., str]
) -> None:
    image_id = upload_image("cat.png", size=(64, 48))

    response = client.get(f"/images/{image_id}")

    body = response.json()
    assert response.status_code == 200
    assert body["original_filename"] == "cat.png"
    assert body["content_type"] == "image/png"
    assert (body["width"], body["height"]) == (64, 48)
    assert body["status"] == "done"
    assert body["thumbnail_urls"] == {
        size: f"/images/{image_id}/thumbnails/{size}" for size in ("128", "256", "512")
    }


def test_get_image_hides_internal_fields(client: TestClient, upload_image: Callable[..., str]) -> None:
    body = client.get(f"/images/{upload_image()}").json()

    assert {"stored_filename", "task_id", "thumbnails"}.isdisjoint(body)


def test_get_unknown_image_returns_404(client: TestClient) -> None:
    assert client.get("/images/no-such-id").status_code == 404


def test_download_file_returns_original_with_detected_content_type(
    client: TestClient, db: Session, upload_image: Callable[..., str], settings: Settings
) -> None:
    image_id = upload_image()
    stored = settings.originals_dir / db.get(Image, image_id).stored_filename

    response = client.get(f"/images/{image_id}/file")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == stored.read_bytes()


def test_download_file_of_unknown_image_returns_404(client: TestClient) -> None:
    assert client.get("/images/no-such-id/file").status_code == 404


@pytest.mark.parametrize("size", [128, 256, 512])
def test_download_thumbnail_returns_webp_of_that_size(
    client: TestClient, upload_image: Callable[..., str], size: int, tmp_path: Path
) -> None:
    image_id = upload_image(size=(1024, 768))

    response = client.get(f"/images/{image_id}/thumbnails/{size}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    downloaded = tmp_path / "thumb.webp"
    downloaded.write_bytes(response.content)
    with PILImage.open(downloaded) as thumbnail:
        assert max(thumbnail.size) == size


def test_download_thumbnail_before_it_is_ready_returns_404(client: TestClient, queued_image: Image) -> None:
    assert client.get(f"/images/{queued_image.id}/thumbnails/128").status_code == 404


def test_download_thumbnail_of_unknown_image_returns_404(client: TestClient) -> None:
    assert client.get("/images/no-such-id/thumbnails/128").status_code == 404


@pytest.mark.parametrize("size", ["64", "abc"])
def test_download_thumbnail_with_unsupported_size_returns_422(
    client: TestClient, upload_image: Callable[..., str], size: str
) -> None:
    assert client.get(f"/images/{upload_image()}/thumbnails/{size}").status_code == 422


def test_patch_image_updates_title_and_description(
    client: TestClient, upload_image: Callable[..., str]
) -> None:
    image_id = upload_image()

    response = client.patch(f"/images/{image_id}", json={"title": "Sleepy cat", "description": "On the sofa"})

    assert response.status_code == 200
    assert (response.json()["title"], response.json()["description"]) == ("Sleepy cat", "On the sofa")
    assert client.get(f"/images/{image_id}").json()["title"] == "Sleepy cat"


def test_patch_image_leaves_unsent_fields_unchanged(
    client: TestClient, upload_image: Callable[..., str]
) -> None:
    image_id = upload_image()
    client.patch(f"/images/{image_id}", json={"title": "Sleepy cat", "description": "On the sofa"})

    response = client.patch(f"/images/{image_id}", json={"title": "Awake cat"})

    assert response.json()["description"] == "On the sofa"


def test_patch_image_with_null_clears_field(client: TestClient, upload_image: Callable[..., str]) -> None:
    image_id = upload_image()
    client.patch(f"/images/{image_id}", json={"title": "Sleepy cat"})

    response = client.patch(f"/images/{image_id}", json={"title": None})

    assert response.json()["title"] is None


@pytest.mark.parametrize(
    "body",
    [{"title": "x" * 101}, {"description": "x" * 1001}, {"status": "done"}, {"stored_filename": "../x.png"}],
)
def test_patch_image_with_invalid_body_returns_422(
    client: TestClient, upload_image: Callable[..., str], body: dict[str, str]
) -> None:
    assert client.patch(f"/images/{upload_image()}", json=body).status_code == 422


def test_patch_unknown_image_returns_404(client: TestClient) -> None:
    assert client.patch("/images/no-such-id", json={"title": "x"}).status_code == 404


def test_delete_image_removes_row_original_and_thumbnails(
    client: TestClient, db: Session, upload_image: Callable[..., str], settings: Settings
) -> None:
    image_id = upload_image()

    response = client.delete(f"/images/{image_id}")

    assert response.status_code == 204
    assert client.get(f"/images/{image_id}").status_code == 404
    assert list(settings.originals_dir.iterdir()) == []
    assert list(settings.thumbnails_dir.iterdir()) == []


def test_delete_image_with_missing_files_returns_204(
    client: TestClient, queued_image: Image, settings: Settings
) -> None:
    (settings.originals_dir / queued_image.stored_filename).unlink()

    assert client.delete(f"/images/{queued_image.id}").status_code == 204


def test_delete_unknown_image_returns_404(client: TestClient) -> None:
    assert client.delete("/images/no-such-id").status_code == 404
