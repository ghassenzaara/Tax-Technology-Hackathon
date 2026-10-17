"""Runtime settings, loaded from environment variables and `backend/.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROFILE_SCHEMA = Path(__file__).parent / "profile" / "profil_schema.json"
PROFILE_DB_DIR = Path(__file__).parent.parent / "db_scheme"  # teammate's SQL (schema 'profil')


class Settings(BaseSettings):
    """All tunables in one place. Env var names match the field names (case-insensitive)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    # OpenAI. The key is required for scanned pages and for extraction.
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

    # Extraction agents (steps 5 to 7).
    # Mirrors the teammate's DB schema in db_scheme/; regenerate when that changes.
    profile_schema_path: Path = PROFILE_SCHEMA
    profile_db_dir: Path = PROFILE_DB_DIR
    # TODO: tune concurrency on real runs; separate from the parse and OCR limits.
    agent_concurrency: int = Field(default=5, gt=0)
    agent_max_attempts: int = Field(default=4, gt=0)
    agent_backoff_min_s: float = Field(default=1.0, ge=0)
    agent_backoff_max_s: float = Field(default=30.0, ge=0)
    # TODO: tune reasoning effort on real documents (none, low, medium, high, xhigh, max).
    extraction_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    # Documents with fewer non-whitespace characters than this get no agent (no cost).
    min_extractable_chars: int = Field(default=20, ge=0)

    # TODO: tune chunking. Token counts are estimated as characters / chars_per_token.
    chars_per_token: float = Field(default=3.5, gt=0)
    chunk_trigger_tokens: int = Field(default=200_000, gt=0)
    chunk_target_tokens: int = Field(default=120_000, gt=0)
    chunk_overlap_pages: int = Field(default=1, ge=0)

    evidence_max_chars: int = Field(default=300, gt=0)

    # TODO: confirm the confidence mapping and caps with the team.
    confidence_high: float = Field(default=0.9, ge=0, le=1)
    confidence_medium: float = Field(default=0.6, ge=0, le=1)
    confidence_low: float = Field(default=0.3, ge=0, le=1)
    confidence_ocr_cap: float = Field(default=0.7, ge=0, le=1)
    confidence_unverified_cap: float = Field(default=0.3, ge=0, le=1)

    # Numeric confidence -> konfidenz enum of the profil schema (hoch / mittel / niedrig).
    # TODO: confirm thresholds with the team.
    konfidenz_hoch_min: float = Field(default=0.8, ge=0, le=1)
    konfidenz_mittel_min: float = Field(default=0.5, ge=0, le=1)

    # TODO: confirm the rounding rule; coarsest granularity from trailing zeros that still
    # counts as rounding (see app/profile/values.py).
    rounding_max_granularity: int = Field(default=1000, gt=0)

    # TODO: Supabase vs local Docker. Both work through database_url with repository=postgres.
    repository: Literal["memory", "postgres"] = "memory"
    database_url: SecretStr | None = None
    db_write_attempts: int = Field(default=3, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
