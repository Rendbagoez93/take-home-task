from pathlib import Path

from PIL import Image

from app.config import settings

EXTENSION_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".gif": "GIF", ".webp": "WEBP"}
ALLOWED_FORMATS = sorted(set(EXTENSION_FORMATS.values()))
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAGIC_HEAD_BYTES = 12  # the longest signature sniff_magic reads (RIFF....WEBP)


class InvalidImageError(Exception):
    pass


def check_extension(filename: str) -> str | None:
    """Return the image format the file extension claims, or None if it is not allowed."""
    return EXTENSION_FORMATS.get(Path(filename).suffix.lower())


def check_content_type(content_type: str) -> bool:
    media_type = content_type.partition(";")[0].strip().lower()
    return media_type in ALLOWED_CONTENT_TYPES


def check_size(size_bytes: int) -> bool:
    return size_bytes <= settings.max_file_bytes


def sniff_magic(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    return None


def verify_image(path: Path) -> tuple[str, int, int]:
    """Decode-check the file with Pillow and return its detected (format, width, height)."""
    try:
        with Image.open(path, formats=ALLOWED_FORMATS) as image:
            # Checked before verify(): open() reads only the header, so a pixel bomb is
            # rejected before Pillow allocates anything for it (ADR-10).
            if image.width * image.height > settings.max_image_pixels:
                raise InvalidImageError("Image has too many pixels")
            image.verify()
        with Image.open(path, formats=ALLOWED_FORMATS) as image:  # verify() leaves the image unusable
            return detected_format(image), image.width, image.height
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise InvalidImageError("File is not a valid image") from error


def detected_format(image: Image.Image) -> str:
    # Phone cameras write multi-picture JPEGs, which Pillow reports as MPO.
    return "JPEG" if image.format == "MPO" else str(image.format)
