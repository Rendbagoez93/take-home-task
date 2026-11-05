import os

# app.config builds its module-level Settings at import time, and Settings fails closed
# without API_KEYS (ADR-16). Set a test key before any app module is imported.
TEST_API_KEY = "test-key"
os.environ["API_KEYS"] = TEST_API_KEY

import struct
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import ExifTags, Image, ImageCms, PngImagePlugin
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.config import settings as app_settings
from app.db import SessionLocal, create_db_engine
from app.models import Base
from app.services.storage import create_storage_dirs
from app.worker.celery_app import celery_app
from main import create_app

# Must be set before the first task runs: the result backend is created once (ADR-15).
celery_app.conf.update(
    task_always_eager=True,
    task_eager_propagates=True,
    task_store_eager_result=True,
    result_backend="cache+memory://",
)

RED = (255, 0, 0)
BLUE = (0, 0, 255)


@pytest.fixture(autouse=True)
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    # Services read the shared app.config.settings, so point that object at tmp_path
    # to keep every test out of the real data/ folder.
    monkeypatch.setattr(app_settings, "data_dir", tmp_path)
    create_storage_dirs()
    return app_settings


@pytest.fixture
def db_engine(settings: Settings) -> Iterator[Engine]:
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    # The API, the lifespan and the Celery task open their own sessions from SessionLocal.
    app_engine = SessionLocal.kw["bind"]
    SessionLocal.configure(bind=engine)
    yield engine
    SessionLocal.configure(bind=app_engine)
    engine.dispose()  # release the SQLite file so Windows can delete tmp_path


@pytest.fixture
def db(db_engine: Engine) -> Iterator[Session]:
    with Session(db_engine) as session:
        yield session


@pytest.fixture
def client(db_engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(), headers={"X-API-Key": TEST_API_KEY}) as test_client:
        yield test_client


@pytest.fixture
def anonymous_client(db_engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def make_image_file(tmp_path: Path) -> Callable[..., Path]:
    def make(
        image_format: str,
        mode: str = "RGB",
        size: tuple[int, int] = (64, 48),
        name: str | None = None,
        color: str | tuple[int, ...] = "green",
        **save_options: object,
    ) -> Path:
        path = tmp_path / (name or f"source.{image_format.lower()}")
        Image.new(mode, size, color).save(path, image_format, **save_options)
        return path

    return make


@pytest.fixture
def srgb_icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def build_exif(orientation: int) -> Image.Exif:
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = orientation
    exif[ExifTags.Base.Make] = "PhoneMaker"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "S"
    gps[ExifTags.GPS.GPSLatitude] = (6.0, 10.0, 30.0)
    return exif


@pytest.fixture
def jpeg_with_gps(make_image_file: Callable[..., Path]) -> Path:
    return make_image_file("JPEG", exif=build_exif(orientation=1), quality=70)


@pytest.fixture
def rotated_jpeg_with_gps(tmp_path: Path) -> Path:
    # Stored landscape, left half red. Orientation 6 means "rotate 90° clockwise to
    # display", so the upright image is portrait with red on top.
    image = Image.new("RGB", (40, 20), BLUE)
    image.paste(RED, (0, 0, 20, 20))
    path = tmp_path / "rotated.jpg"
    image.save(path, "JPEG", exif=build_exif(orientation=6))
    return path


@pytest.fixture
def png_with_metadata(tmp_path: Path) -> Path:
    text_chunks = PngImagePlugin.PngInfo()
    text_chunks.add_text("Comment", "taken at home")
    text_chunks.add_itxt("Author", "Jane Doe")
    path = tmp_path / "meta.png"
    Image.new("RGB", (64, 48), "green").save(
        path, "PNG", pnginfo=text_chunks, exif=build_exif(orientation=1)
    )
    return path


@pytest.fixture
def animated_gif(tmp_path: Path) -> Path:
    frames = [Image.new("P", (64, 48), color) for color in (1, 2, 3)]
    for frame in frames:
        frame.putpalette([0, 0, 0, *RED, *BLUE, 0, 255, 0])
    path = tmp_path / "animated.gif"
    frames[0].save(
        path, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0, comment=b"secret"
    )
    return path


@pytest.fixture
def animated_webp(tmp_path: Path) -> Path:
    frames = [Image.new("RGB", (64, 48), color) for color in (RED, BLUE, "green")]
    path = tmp_path / "animated.webp"
    frames[0].save(path, "WEBP", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return path


@pytest.fixture
def text_as_png(tmp_path: Path) -> Path:
    path = tmp_path / "notes.png"
    path.write_bytes(b"hello, this is not an image")
    return path


@pytest.fixture
def truncated_png(tmp_path: Path) -> Path:
    # Noise, so that cutting the file loses pixel data and not just the zlib checksum.
    path = tmp_path / "truncated.png"
    Image.effect_noise((256, 256), 64).save(path, "PNG")
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    return path


@pytest.fixture
def empty_file(tmp_path: Path) -> Path:
    path = tmp_path / "empty.png"
    path.write_bytes(b"")
    return path


@pytest.fixture
def oversized_png(make_image_file: Callable[..., Path], settings: Settings) -> Path:
    path = make_image_file("PNG")
    padding = settings.max_file_bytes + 1 - path.stat().st_size
    with path.open("ab") as file:
        file.write(b"\0" * padding)
    return path


@pytest.fixture
def pixel_bomb_png(tmp_path: Path) -> Path:
    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data)
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", 100_000, 100_000, 8, 2, 0, 0, 0)  # 10 gigapixels, RGB
    path = tmp_path / "bomb.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b""))
    return path
