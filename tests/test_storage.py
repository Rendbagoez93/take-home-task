from pathlib import Path

from app.config import Settings
from app.models import Image
from app.services.storage import delete_image_files, new_temp_path, promote


def test_new_temp_path_is_unique_and_inside_temp_dir(settings: Settings) -> None:
    first, second = new_temp_path(), new_temp_path()

    assert first != second
    assert first.parent == settings.temp_dir
    assert not first.exists()


def test_promote_moves_temp_file_to_originals_with_uuid_name(settings: Settings) -> None:
    temp_path = new_temp_path()
    temp_path.write_bytes(b"image bytes")

    stored_name = promote(temp_path, "png")

    assert not temp_path.exists()
    assert stored_name.endswith(".png")
    assert len(Path(stored_name).stem) == 36
    assert (settings.originals_dir / stored_name).read_bytes() == b"image bytes"


def test_delete_image_files_removes_original_and_its_thumbnails(settings: Settings) -> None:
    (settings.originals_dir / "abc.png").write_bytes(b"original")
    for size in (128, 256):
        (settings.thumbnails_dir / f"abc_{size}.webp").write_bytes(b"thumbnail")
    other_thumbnail = settings.thumbnails_dir / "xyz_128.webp"
    other_thumbnail.write_bytes(b"thumbnail")

    delete_image_files(Image(stored_filename="abc.png"))

    assert not (settings.originals_dir / "abc.png").exists()
    assert list(settings.thumbnails_dir.iterdir()) == [other_thumbnail]


def test_delete_image_files_ignores_missing_files() -> None:
    delete_image_files(Image(stored_filename="already-gone.png"))
