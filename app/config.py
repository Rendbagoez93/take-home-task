from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Required and non-empty: the app refuses to start without keys (fail closed, ADR-16).
    api_keys: Annotated[list[str], NoDecode] = Field(min_length=1)
    data_dir: Path = Path("data")
    upload_session_ttl_seconds: int = 600
    max_file_bytes: int = 1024 * 1024
    max_files_per_request: int = 3
    # Room for multipart boundaries, part headers and form fields on top of the file bytes.
    max_request_overhead_bytes: int = 64 * 1024
    # Upload progress is written to SQLite only after this much growth (ADR-03).
    progress_write_min_bytes: int = 64 * 1024
    progress_write_min_fraction: float = 0.05
    # Decompression-bomb guard (ADR-10): 40 MP is ~160 MB once decoded as RGBA.
    max_image_pixels: int = 40_000_000
    thumbnail_sizes: list[int] = [128, 256, 512]
    default_page_size: int = 20
    max_page_size: int = 100
    # Celery results expire; the DB keeps the permanent status (ADR-07).
    celery_result_expires_seconds: int = 24 * 60 * 60
    thumbnail_task_max_retries: int = 5

    @field_validator("api_keys", mode="before")
    @classmethod
    def split_comma_separated(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return [key.strip() for key in value.split(",") if key.strip()]

    @property
    def max_request_bytes(self) -> int:
        return self.max_files_per_request * self.max_file_bytes + self.max_request_overhead_bytes

    @property
    def originals_dir(self) -> Path:
        return self.data_dir / "uploads" / "originals"

    @property
    def thumbnails_dir(self) -> Path:
        return self.data_dir / "uploads" / "thumbnails"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "uploads" / "tmp"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir.as_posix()}/app.db"

    @property
    def celery_broker_url(self) -> str:
        return f"sqla+sqlite:///{self.data_dir.as_posix()}/celery-broker.db"

    @property
    def celery_result_backend(self) -> str:
        return f"db+sqlite:///{self.data_dir.as_posix()}/celery-results.db"


settings = Settings()
