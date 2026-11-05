from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import ExifTags, Image

from app.services.sanitize import is_lossless_webp, strip_metadata
from app.services.validation import InvalidImageError

MakeImageFile = Callable[..., Path]

TRANSPARENT_GREEN = (0, 128, 0, 0)


def test_strip_metadata_removes_exif_and_gps_from_jpeg(jpeg_with_gps: Path) -> None:
    with Image.open(jpeg_with_gps) as before:
        assert before.getexif().get_ifd(ExifTags.IFD.GPSInfo)

    strip_metadata(jpeg_with_gps, "JPEG")

    with Image.open(jpeg_with_gps) as after:
        assert not after.getexif()
        assert "exif" not in after.info


def test_strip_metadata_keeps_jpeg_quantization_when_not_rotated(jpeg_with_gps: Path) -> None:
    with Image.open(jpeg_with_gps) as before:
        tables_before = before.quantization

    strip_metadata(jpeg_with_gps, "JPEG")

    with Image.open(jpeg_with_gps) as after:
        assert after.quantization == tables_before


def test_strip_metadata_returns_size_after_stripping(jpeg_with_gps: Path) -> None:
    size_before = jpeg_with_gps.stat().st_size

    width, height, size_bytes = strip_metadata(jpeg_with_gps, "JPEG")

    assert (width, height) == (64, 48)
    assert size_bytes == jpeg_with_gps.stat().st_size
    assert size_bytes < size_before


def test_strip_metadata_applies_exif_rotation_to_pixels(rotated_jpeg_with_gps: Path) -> None:
    width, height, _ = strip_metadata(rotated_jpeg_with_gps, "JPEG")

    with Image.open(rotated_jpeg_with_gps) as after:
        assert (width, height) == (20, 40)
        assert after.size == (20, 40)
        assert not after.getexif()
        red, green, blue = after.getpixel((10, 5))
        assert red > 200 and blue < 50


def test_strip_metadata_saves_multi_picture_jpeg_as_plain_jpeg(tmp_path: Path) -> None:
    path = tmp_path / "phone.jpg"
    frames = [Image.new("RGB", (64, 48), color) for color in ("red", "blue")]
    frames[0].save(path, "MPO", save_all=True, append_images=frames[1:])

    assert strip_metadata(path, "JPEG")[:2] == (64, 48)
    with Image.open(path) as after:
        assert after.format == "JPEG"


def test_strip_metadata_removes_png_text_chunks_and_exif(png_with_metadata: Path) -> None:
    with Image.open(png_with_metadata) as before:
        assert before.text
        assert before.getexif()

    strip_metadata(png_with_metadata, "PNG")

    with Image.open(png_with_metadata) as after:
        assert after.text == {}
        assert not after.getexif()


def test_strip_metadata_keeps_png_transparency(make_image_file: MakeImageFile) -> None:
    path = make_image_file("PNG", mode="RGBA", color=TRANSPARENT_GREEN)

    strip_metadata(path, "PNG")

    with Image.open(path) as after:
        assert after.mode == "RGBA"


def test_strip_metadata_keeps_gif_frames_and_drops_comment(animated_gif: Path) -> None:
    strip_metadata(animated_gif, "GIF")

    with Image.open(animated_gif) as after:
        assert after.n_frames == 3
        assert after.info["duration"] == 100
        assert after.info["loop"] == 0
        assert "comment" not in after.info


def test_strip_metadata_keeps_animated_webp_frames_and_loop(animated_webp: Path) -> None:
    strip_metadata(animated_webp, "WEBP")

    with Image.open(animated_webp) as after:
        assert after.n_frames == 3
        assert after.info["loop"] == 0


def test_strip_metadata_removes_webp_exif_and_xmp(make_image_file: MakeImageFile) -> None:
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "PhoneMaker"
    path = make_image_file("WEBP", exif=exif, xmp=b"<x:xmpmeta>secret</x:xmpmeta>")

    strip_metadata(path, "WEBP")

    with Image.open(path) as after:
        assert "exif" not in after.info
        assert "xmp" not in after.info


@pytest.mark.parametrize("lossless", [True, False])
def test_strip_metadata_keeps_webp_lossless_mode(
    lossless: bool, make_image_file: MakeImageFile
) -> None:
    path = make_image_file("WEBP", lossless=lossless)
    assert is_lossless_webp(path) is lossless

    strip_metadata(path, "WEBP")

    assert is_lossless_webp(path) is lossless


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_strip_metadata_keeps_icc_profile(
    image_format: str, make_image_file: MakeImageFile, srgb_icc: bytes
) -> None:
    path = make_image_file(image_format, icc_profile=srgb_icc)

    strip_metadata(path, image_format)

    with Image.open(path) as after:
        assert after.info.get("icc_profile") == srgb_icc


def test_strip_metadata_failure_leaves_original_untouched(truncated_png: Path) -> None:
    original_bytes = truncated_png.read_bytes()

    with pytest.raises(InvalidImageError):
        strip_metadata(truncated_png, "PNG")

    assert truncated_png.read_bytes() == original_bytes
    assert list(truncated_png.parent.glob("*.clean")) == []
