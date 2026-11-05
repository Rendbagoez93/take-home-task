from collections.abc import Callable
from pathlib import Path

from PIL import Image

from app.config import Settings
from app.services.thumbnails import make_thumbnails

MakeImageFile = Callable[..., Path]

TRANSPARENT_GREEN = (0, 128, 0, 0)


def test_make_thumbnails_writes_one_webp_per_size(
    make_image_file: MakeImageFile, settings: Settings
) -> None:
    source = make_image_file("PNG", size=(1000, 500), name="0b6f.png")

    thumbnails = make_thumbnails(source, settings.thumbnail_sizes, settings.thumbnails_dir)

    assert thumbnails == {
        size: settings.thumbnails_dir / f"0b6f_{size}.webp" for size in (128, 256, 512)
    }
    for path in thumbnails.values():
        with Image.open(path) as thumbnail:
            assert thumbnail.format == "WEBP"


def test_make_thumbnails_keeps_aspect_ratio(
    make_image_file: MakeImageFile, settings: Settings
) -> None:
    source = make_image_file("PNG", size=(1000, 500))

    thumbnails = make_thumbnails(source, [128, 512], settings.thumbnails_dir)

    with Image.open(thumbnails[128]) as small, Image.open(thumbnails[512]) as large:
        assert small.size == (128, 64)
        assert large.size == (512, 256)


def test_make_thumbnails_keeps_transparency(
    make_image_file: MakeImageFile, settings: Settings
) -> None:
    source = make_image_file("PNG", mode="RGBA", size=(300, 300), color=TRANSPARENT_GREEN)

    thumbnails = make_thumbnails(source, [128], settings.thumbnails_dir)

    with Image.open(thumbnails[128]) as thumbnail:
        assert thumbnail.mode == "RGBA"


def test_make_thumbnails_applies_exif_rotation(
    rotated_jpeg_with_gps: Path, settings: Settings
) -> None:
    thumbnails = make_thumbnails(rotated_jpeg_with_gps, [128], settings.thumbnails_dir)

    with Image.open(thumbnails[128]) as thumbnail:
        assert thumbnail.width < thumbnail.height


def test_make_thumbnails_uses_first_gif_frame(animated_gif: Path, settings: Settings) -> None:
    thumbnails = make_thumbnails(animated_gif, [128], settings.thumbnails_dir)

    with Image.open(thumbnails[128]) as thumbnail:
        assert getattr(thumbnail, "n_frames", 1) == 1
        red, green, blue = thumbnail.convert("RGB").getpixel((10, 10))
        assert red > 200 and blue < 50


def test_make_thumbnails_overwrites_existing_files(
    make_image_file: MakeImageFile, settings: Settings
) -> None:
    source = make_image_file("PNG", size=(1000, 500))

    first = make_thumbnails(source, [128], settings.thumbnails_dir)
    second = make_thumbnails(source, [128], settings.thumbnails_dir)

    assert first == second
    assert len(list(settings.thumbnails_dir.iterdir())) == 1


def test_make_thumbnails_reports_progress_per_size(
    make_image_file: MakeImageFile, settings: Settings
) -> None:
    source = make_image_file("PNG", size=(1000, 500))
    calls: list[tuple[int, int]] = []

    make_thumbnails(
        source,
        [128, 256, 512],
        settings.thumbnails_dir,
        on_progress=lambda current, total: calls.append((current, total)),
    )

    assert calls == [(1, 3), (2, 3), (3, 3)]
