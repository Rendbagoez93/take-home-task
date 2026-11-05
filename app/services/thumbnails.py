from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageOps


def make_thumbnails(
    src: Path,
    sizes: list[int],
    out_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[int, Path]:
    with Image.open(src) as image:
        # Opening a GIF lands on frame 0, and exif_transpose returns a single-frame copy.
        upright = ImageOps.exif_transpose(image)
    mode = "RGBA" if upright.has_transparency_data else "RGB"
    upright = upright.convert(mode)

    thumbnails: dict[int, Path] = {}
    for index, size in enumerate(sizes, start=1):
        thumbnail = upright.copy()
        thumbnail.thumbnail((size, size))
        path = out_dir / f"{src.stem}_{size}.webp"
        thumbnail.save(path, "WEBP")
        thumbnails[size] = path
        if on_progress is not None:
            on_progress(index, len(sizes))
    return thumbnails
