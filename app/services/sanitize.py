import os
from pathlib import Path

from PIL import ExifTags, Image, ImageOps

from app.services.validation import InvalidImageError

LOSSY_REENCODE_QUALITY = 90
# Pillow falls back to the source's own metadata for some of these keys (JPEG comment,
# GIF frame-0 comment), so every save passes them explicitly as empty.
NO_METADATA = {"exif": b"", "xmp": b"", "comment": b""}

RIFF_HEADER_BYTES = 12
CHUNK_HEADER_BYTES = 8
ANMF_FRAME_HEADER_BYTES = 16


def strip_metadata(path: Path, image_format: str) -> tuple[int, int, int]:
    """Re-save the image in place without EXIF/GPS/XMP/comments; return (width, height, size_bytes)."""
    clean_path = path.with_name(f"{path.name}.clean")
    try:
        with Image.open(path, formats=[image_format]) as image:
            match image_format:
                case "JPEG":
                    save_jpeg(image, clean_path)
                case "PNG":
                    save_png(image, clean_path)
                case "GIF":
                    save_gif(image, clean_path)
                case "WEBP":
                    save_webp(image, clean_path, lossless=is_lossless_webp(path))
                case _:
                    raise InvalidImageError(f"Unsupported image format {image_format}")
        os.replace(clean_path, path)  # the original stays intact if saving fails (ADR-17)
    except (OSError, SyntaxError, ValueError) as error:
        raise InvalidImageError("Image could not be re-saved without metadata") from error
    finally:
        clean_path.unlink(missing_ok=True)

    with Image.open(path) as image:
        return image.width, image.height, path.stat().st_size


def save_jpeg(image: Image.Image, clean_path: Path) -> None:
    icc_profile = image.info.get("icc_profile")
    is_upright = image.getexif().get(ExifTags.Base.Orientation, 1) == 1
    # "keep" reuses the original quantization tables, so quality barely changes. Pillow
    # allows it only on a plain JPEG: not on a rotated copy or a multi-picture (MPO) file.
    if is_upright and image.format == "JPEG":
        image.save(
            clean_path, "JPEG", quality="keep", subsampling="keep", icc_profile=icc_profile, **NO_METADATA
        )
        return
    upright = ImageOps.exif_transpose(image)
    upright.save(
        clean_path, "JPEG", quality=LOSSY_REENCODE_QUALITY, icc_profile=icc_profile, **NO_METADATA
    )


def save_png(image: Image.Image, clean_path: Path) -> None:
    upright = ImageOps.exif_transpose(image)
    upright.save(clean_path, "PNG", icc_profile=image.info.get("icc_profile"), **NO_METADATA)


def save_gif(image: Image.Image, clean_path: Path) -> None:
    image.save(clean_path, "GIF", save_all=True, **NO_METADATA)


def save_webp(image: Image.Image, clean_path: Path, lossless: bool) -> None:
    icc_profile = image.info.get("icc_profile")
    loop = image.info.get("loop", 0)
    if image.n_frames == 1:  # exif_transpose would flatten an animation to one frame
        image = ImageOps.exif_transpose(image)
    image.save(
        clean_path,
        "WEBP",
        save_all=True,
        lossless=lossless,
        quality=LOSSY_REENCODE_QUALITY,
        loop=loop,  # WebP's save ignores the source loop count and ICC profile
        icc_profile=icc_profile,
        **NO_METADATA,
    )


def is_lossless_webp(path: Path) -> bool:
    """True when the first image chunk is VP8L (lossless) rather than VP8 (lossy)."""
    data = path.read_bytes()
    offset = RIFF_HEADER_BYTES
    while offset + CHUNK_HEADER_BYTES <= len(data):
        fourcc = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        if fourcc in (b"VP8 ", b"VP8L"):
            return fourcc == b"VP8L"
        if fourcc == b"ANMF":
            # An animation frame holds its own image chunks after a fixed header.
            offset += CHUNK_HEADER_BYTES + ANMF_FRAME_HEADER_BYTES
            continue
        offset += CHUNK_HEADER_BYTES + chunk_size + chunk_size % 2  # chunks are padded to even size
    return False
