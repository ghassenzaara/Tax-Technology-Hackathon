"""Runtime settings, loaded from environment variables and `backend/.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunables in one place. Env var names match the field names (case-insensitive)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    # OpenAI. The key is only required when a scanned page has to be transcribed.
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6-luna"
    openai_timeout_s: float = Field(default=120.0, gt=0)

    # TODO: tune scan threshold (non-whitespace chars per page) and render DPI on real documents.
    scan_min_chars: int = Field(default=50, ge=0)
    ocr_dpi: int = Field(default=200, gt=0)

    # TODO: confirm concurrency values (brief suggests 5 to 10).
    parse_concurrency: int = Field(default=5, gt=0)
    ocr_concurrency: int = Field(default=5, gt=0)

    ocr_max_attempts: int = Field(default=4, gt=0)
    ocr_backoff_min_s: float = Field(default=1.0, ge=0)
    ocr_backoff_max_s: float = Field(default=30.0, ge=0)

    # TODO: size and cost guards are placeholders.
    max_file_mb: float = Field(default=50, gt=0)
    ocr_page_cap: int = Field(default=30, ge=0)
    excel_row_cap: int = Field(default=2000, gt=0)

    data_dir: Path = Path("data")


@lru_cache
def get_settings() -> Settings:
    return Settings()
