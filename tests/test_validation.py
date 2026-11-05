from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from app.config import Settings
from app.services.validation import (
    InvalidImageError,
    check_content_type,
    check_extension,
    check_size,
    sniff_magic,
    verify_image,
)

MakeImageFile = Callable[..., Path]

FORMATS = ["JPEG", "PNG", "GIF", "WEBP"]


@pytest.mark.parametrize(
    ("filename", "image_format"),
    [
        ("cat.jpg", "JPEG"),
        ("cat.jpeg", "JPEG"),
        ("cat.png", "PNG"),
        ("cat.gif", "GIF"),
        ("cat.webp", "WEBP"),
        ("CAT.PNG", "PNG"),
    ],
)
def test_check_extension_returns_claimed_format(filename: str, image_format: str) -> None:
    assert check_extension(filename) == image_format


@pytest.mark.parametrize("filename", ["notes.txt", "cat", "cat.png.exe", "cat.svg", ".png", ""])
def test_check_extension_rejects_disallowed(filename: str) -> None:
    assert check_extension(filename) is None


@pytest.mark.parametrize(
    "content_type", ["image/jpeg", "image/png", "image/gif", "image/webp", "IMAGE/PNG"]
)
def test_check_content_type_accepts_image_types(content_type: str) -> None:
    assert check_content_type(content_type) is True


@pytest.mark.parametrize(
    "content_type", ["text/plain", "image/svg+xml", "application/octet-stream", ""]
)
def test_check_content_type_rejects_other_types(content_type: str) -> None:
    assert check_content_type(content_type) is False


@pytest.mark.parametrize("image_format", FORMATS)
def test_sniff_magic_detects_real_format(
    image_format: str, make_image_file: MakeImageFile
) -> None:
    head = make_image_file(image_format).read_bytes()[:12]

    assert sniff_magic(head) == image_format


def test_sniff_magic_rejects_text_renamed_to_png(text_as_png: Path) -> None:
    assert sniff_magic(text_as_png.read_bytes()[:12]) is None


def test_sniff_magic_rejects_empty_head() -> None:
    assert sniff_magic(b"") is None


def test_sniff_magic_rejects_riff_that_is_not_webp() -> None:
    assert sniff_magic(b"RIFF\x00\x00\x00\x00WAVE") is None


def test_check_size_accepts_exactly_max(settings: Settings) -> None:
    assert check_size(settings.max_file_bytes) is True


def test_check_size_rejects_one_byte_over_max(oversized_png: Path, settings: Settings) -> None:
    assert oversized_png.stat().st_size == settings.max_file_bytes + 1
    assert check_size(oversized_png.stat().st_size) is False


@pytest.mark.parametrize("image_format", FORMATS)
def test_verify_image_returns_detected_format_and_size(
    image_format: str, make_image_file: MakeImageFile
) -> None:
    path = make_image_file(image_format, size=(64, 48))

    assert verify_image(path) == (image_format, 64, 48)


def test_verify_image_accepts_rgba_png(make_image_file: MakeImageFile) -> None:
    assert verify_image(make_image_file("PNG", mode="RGBA")) == ("PNG", 64, 48)


def test_verify_image_reports_multi_picture_jpeg_as_jpeg(tmp_path: Path) -> None:
    path = tmp_path / "phone.jpg"
    frames = [Image.new("RGB", (64, 48), color) for color in ("red", "blue")]
    frames[0].save(path, "MPO", save_all=True, append_images=frames[1:])

    assert verify_image(path) == ("JPEG", 64, 48)


@pytest.mark.parametrize(
    "bad_file", ["text_as_png", "truncated_png", "empty_file", "pixel_bomb_png"]
)
def test_verify_image_rejects_bad_file(bad_file: str, request: pytest.FixtureRequest) -> None:
    with pytest.raises(InvalidImageError):
        verify_image(request.getfixturevalue(bad_file))


def test_verify_image_rejects_format_outside_allowlist(make_image_file: MakeImageFile) -> None:
    with pytest.raises(InvalidImageError):
        verify_image(make_image_file("BMP"))


def test_verify_image_rejects_more_pixels_than_limit(
    make_image_file: MakeImageFile, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(settings, "max_image_pixels", 64 * 48 - 1)

    with pytest.raises(InvalidImageError):
        verify_image(make_image_file("PNG", size=(64, 48)))
