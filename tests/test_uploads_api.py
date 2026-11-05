from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import ExifTags
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Image, ImageStatus, Upload, UploadStatus, utcnow

FileTuple = tuple[str, bytes, str]


def open_session(client: TestClient) -> str:
    return client.post("/uploads").json()["upload_id"]


def upload_files(client: TestClient, upload_id: str, *files: FileTuple) -> httpx.Response:
    return client.post(f"/uploads/{upload_id}/files", files=[("files", file) for file in files])


def rejected_codes(response: httpx.Response) -> dict[str, str]:
    return {file["filename"]: file["code"] for file in response.json()["rejected"]}


@pytest.fixture
def png(make_image_file: Callable[..., Path]) -> Callable[[str], FileTuple]:
    def make(filename: str) -> FileTuple:
        return filename, make_image_file("PNG", name=filename).read_bytes(), "image/png"

    return make


def test_create_upload_returns_session_that_expires_in_ten_minutes(client: TestClient) -> None:
    response = client.post("/uploads")

    body = response.json()
    assert response.status_code == 201
    assert body["status"] == "created"
    expires_in = datetime.fromisoformat(body["expires_at"]) - utcnow()
    assert timedelta(minutes=9) < expires_in <= timedelta(minutes=10)


def test_upload_three_valid_files_accepts_all(
    client: TestClient, png: Callable[[str], FileTuple], db: Session, settings: Settings
) -> None:
    upload_id = open_session(client)

    response = upload_files(client, upload_id, png("a.png"), png("b.png"), png("c.png"))

    assert response.status_code == 202
    assert [file["filename"] for file in response.json()["accepted"]] == ["a.png", "b.png", "c.png"]
    assert response.json()["rejected"] == []
    images = db.scalars(select(Image)).all()
    assert {image.status for image in images} == {ImageStatus.done}
    assert len(list(settings.originals_dir.iterdir())) == 3


def test_upload_mixed_files_accepts_valid_and_rejects_invalid(
    client: TestClient,
    png: Callable[[str], FileTuple],
    text_as_png: Path,
    truncated_png: Path,
) -> None:
    upload_id = open_session(client)

    response = upload_files(
        client,
        upload_id,
        png("good.png"),
        ("notes.png", text_as_png.read_bytes(), "image/png"),
        ("truncated.png", truncated_png.read_bytes(), "image/png"),
    )

    assert [file["filename"] for file in response.json()["accepted"]] == ["good.png"]
    assert rejected_codes(response) == {"notes.png": "unsupported_type", "truncated.png": "invalid_image"}


def test_upload_oversized_middle_file_is_rejected_while_others_pass(
    client: TestClient, png: Callable[[str], FileTuple], oversized_png: Path
) -> None:
    upload_id = open_session(client)

    response = upload_files(
        client, upload_id, png("first.png"), ("big.png", oversized_png.read_bytes(), "image/png"), png("third.png")
    )

    assert [file["filename"] for file in response.json()["accepted"]] == ["first.png", "third.png"]
    assert rejected_codes(response) == {"big.png": "too_large"}


def test_upload_four_files_rejects_the_fourth(client: TestClient, png: Callable[[str], FileTuple]) -> None:
    upload_id = open_session(client)

    response = upload_files(client, upload_id, png("1.png"), png("2.png"), png("3.png"), png("4.png"))

    assert len(response.json()["accepted"]) == 3
    assert rejected_codes(response) == {"4.png": "too_many_files"}


def test_upload_content_that_does_not_match_extension_is_invalid_image(
    client: TestClient, png: Callable[[str], FileTuple]
) -> None:
    upload_id = open_session(client)
    _, png_content, _ = png("real.png")

    response = upload_files(client, upload_id, ("fake.jpg", png_content, "image/jpeg"))

    assert rejected_codes(response) == {"fake.jpg": "invalid_image"}


def test_upload_rejected_files_leave_no_temp_files(
    client: TestClient, text_as_png: Path, truncated_png: Path, settings: Settings
) -> None:
    upload_id = open_session(client)

    upload_files(
        client,
        upload_id,
        ("notes.png", text_as_png.read_bytes(), "image/png"),
        ("truncated.png", truncated_png.read_bytes(), "image/png"),
    )

    assert list(settings.temp_dir.iterdir()) == []
    assert list(settings.originals_dir.iterdir()) == []


def test_upload_stored_original_has_no_exif_or_gps(
    client: TestClient, jpeg_with_gps: Path, db: Session, settings: Settings
) -> None:
    upload_id = open_session(client)

    upload_files(client, upload_id, ("phone.jpg", jpeg_with_gps.read_bytes(), "image/jpeg"))

    image = db.scalars(select(Image)).one()
    with PILImage.open(settings.originals_dir / image.stored_filename) as stored:
        exif = stored.getexif()
        assert not exif
        assert not exif.get_ifd(ExifTags.IFD.GPSInfo)
    assert image.content_type == "image/jpeg"


def test_upload_reused_session_returns_409(client: TestClient, png: Callable[[str], FileTuple]) -> None:
    upload_id = open_session(client)
    upload_files(client, upload_id, png("a.png"))

    response = upload_files(client, upload_id, png("b.png"))

    assert response.status_code == 409


def test_upload_unknown_session_returns_404(client: TestClient, png: Callable[[str], FileTuple]) -> None:
    response = upload_files(client, "no-such-session", png("a.png"))

    assert response.status_code == 404


def test_upload_expired_session_returns_410(
    client: TestClient, png: Callable[[str], FileTuple], db: Session
) -> None:
    upload_id = open_session(client)
    db.get_one(Upload, upload_id).expires_at = utcnow() - timedelta(seconds=1)
    db.commit()

    response = upload_files(client, upload_id, png("a.png"))

    assert response.status_code == 410


def test_upload_without_content_length_returns_411(client: TestClient) -> None:
    upload_id = open_session(client)

    def chunked_body() -> Iterator[bytes]:
        yield b"--boundary--\r\n"

    response = client.post(
        f"/uploads/{upload_id}/files",
        content=chunked_body(),
        headers={"Content-Type": "multipart/form-data; boundary=boundary"},
    )

    assert response.status_code == 411


def test_upload_request_larger_than_limit_returns_413(client: TestClient, settings: Settings) -> None:
    upload_id = open_session(client)

    response = client.post(
        f"/uploads/{upload_id}/files",
        content=b"x" * (settings.max_request_bytes + 1),
        headers={"Content-Type": "multipart/form-data; boundary=boundary"},
    )

    assert response.status_code == 413


def test_upload_without_boundary_returns_400(client: TestClient) -> None:
    upload_id = open_session(client)

    response = client.post(
        f"/uploads/{upload_id}/files", content=b"abc", headers={"Content-Type": "multipart/form-data"}
    )

    assert response.status_code == 400


def test_upload_malformed_body_returns_400_and_fails_session(client: TestClient, db: Session) -> None:
    upload_id = open_session(client)

    response = client.post(
        f"/uploads/{upload_id}/files",
        content=b"--boundary\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.png\"\r\n",
        headers={"Content-Type": "multipart/form-data; boundary=boundary"},
    )

    assert response.status_code == 400
    assert db.get_one(Upload, upload_id).status == UploadStatus.failed


def test_get_upload_reports_bytes_images_and_rejected(
    client: TestClient, png: Callable[[str], FileTuple], text_as_png: Path
) -> None:
    upload_id = open_session(client)
    upload_files(client, upload_id, png("a.png"), ("notes.png", text_as_png.read_bytes(), "image/png"))

    body = client.get(f"/uploads/{upload_id}").json()

    assert body["status"] == "completed"
    assert body["upload"]["percent"] == 100.0
    assert [(image["filename"], image["status"]) for image in body["images"]] == [("a.png", "done")]
    assert body["images"][0]["progress"]["percent"] == 100.0
    assert [file["code"] for file in body["rejected"]] == ["unsupported_type"]


def test_get_upload_unknown_returns_404(client: TestClient) -> None:
    assert client.get("/uploads/no-such-session").status_code == 404


def test_upload_files_route_documents_multipart_body_in_swagger(client: TestClient, settings: Settings) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/uploads/{upload_id}/files"]["post"]

    schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert schema["properties"]["files"]["maxItems"] == settings.max_files_per_request
