from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_without_api_keys_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_KEYS", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_with_empty_api_keys_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEYS", "")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_api_keys_are_read_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEYS", "key-one, key-two")

    assert Settings(_env_file=None).api_keys == ["key-one", "key-two"]


def test_settings_session_ttl_defaults_to_ten_minutes(settings: Settings) -> None:
    assert settings.upload_session_ttl_seconds == 600


def test_settings_file_paths_live_under_data_dir(settings: Settings, tmp_path: Path) -> None:
    assert settings.originals_dir == tmp_path / "uploads" / "originals"
    assert settings.thumbnails_dir == tmp_path / "uploads" / "thumbnails"
    assert settings.temp_dir == tmp_path / "uploads" / "tmp"


def test_settings_databases_are_separate_files_under_data_dir(
    settings: Settings, tmp_path: Path
) -> None:
    data = tmp_path.as_posix()

    assert settings.database_url == f"sqlite:///{data}/app.db"
    assert settings.celery_broker_url == f"sqla+sqlite:///{data}/celery-broker.db"
    assert settings.celery_result_backend == f"db+sqlite:///{data}/celery-results.db"
