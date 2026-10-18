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

    # Uploads live below this folder; step 10 deletes files only inside it.
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

    # Step 9: one descriptive sentence and one vector per feature.
    # TODO: verify the embedding model id and its dimension (the schema README assumes 1536).
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = Field(default=1536, gt=0)
    # TODO: tune batch size, sentence caps, reasoning effort and concurrency on real runs.
    embedding_batch_size: int = Field(default=64, gt=0)
    sentence_max_words: int = Field(default=40, gt=0)
    sentence_max_chars: int = Field(default=400, gt=0)
    sentence_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    embed_concurrency: int = Field(default=5, gt=0)
    # A busy marker older than this counts as left over from a crash (confirm, embed, cleanup).
    # TODO: tune the busy lock.
    step_lock_timeout_s: float = Field(default=3600, gt=0)
    # Statute of a norm -> tax types for the embedding filter metadata (steuerarten).
    # TODO: complete and review the mapping (LSt, AO and other statutes).
    statute_tax_types: dict[str, list[str]] = {
        "KStG": ["KSt"],
        "GewStG": ["GewSt"],
        "UStG": ["USt"],
        "GrEStG": ["GrESt"],
        "EStG": ["ESt"],
    }

    # Steps 11 and 12: one law PDF, split into sections, one vector per section.
    # TODO: tune the metadata pages and the section token range after the first real law.
    law_metadata_pages: int = Field(default=3, gt=0)
    law_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    section_min_tokens: int = Field(default=80, gt=0)
    section_target_tokens: int = Field(default=400, gt=0)
    section_max_tokens: int = Field(default=800, gt=0)

    # Steps 13 to 15: rule filter, max cosine score, LLM judge.
    # BFH I R 1/23 + Nordhafen: 0.54 retains all 5 observed hits, 17 rather than 20 candidates.
    # TODO: validate this initial calibration and cap on more labelled source/profile pairs.
    match_score_threshold: float = Field(default=0.54, ge=-1, le=1)
    match_max_candidates: int = Field(default=20, gt=0)
    # TODO: tune features per section, judge concurrency and reasoning effort.
    judge_features_per_section: int = Field(default=3, gt=0)
    judge_concurrency: int = Field(default=5, gt=0)
    judge_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    # Re-screen right after an advisor update (default off until the API triggers it).
    screen_after_update: bool = False
    # A legal source uploaded through the API is screened against every eligible company
    # in the same job, so a relevant new law alerts the advisor without another call.
    screen_after_ingest: bool = True
    law_source_dir: Path = Path(__file__).resolve().parents[1] / "mock_laws"
    sender_salutation: str | None = None

    # Single-user platform (one Steuerberater, no login): the label stamped as bestaetigt_von
    # and in the audit log by the HTTP API, which takes no person from the caller.
    advisor_label: str = Field(default="Steuerberater", min_length=1)
    # Prototype only: Einstellungen offers "reset all clients and screening results".
    demo_reset_enabled: bool = True

    # Outreach (16 to 18). TODO: review text/signature, caps and timeout with the advisor.
    mail_mode: Literal["log", "smtp"] = "log"
    gmail_address: str = ""
    gmail_app_password: SecretStr | None = None
    mail_redirect_to: str | None = None
    sender_name: str = "TODO: Name des Steuerberaters"
    sender_firm: str = "TODO: Name der Kanzlei"
    smtp_timeout_s: float = Field(default=20, gt=0)
    mail_subject_max_chars: int = Field(default=200, gt=0)
    mail_body_max_chars: int = Field(default=5000, gt=0)
    mail_recipient_max_chars: int = Field(default=254, gt=0, le=254)

    # HTTP API and upload layer (step 0).
    # TODO: hosting and start command outside local development (deferred with deployment).
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, gt=0, lt=65536)
    # TODO: the UI team's dev URL and port.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]
    # TODO: tune upload limits (per-file size is max_file_mb above) and the global run cap.
    upload_max_total_mb: float = Field(default=500, gt=0)
    upload_max_files: int = Field(default=300, gt=0)
    upload_max_path_chars: int = Field(default=240, gt=0)
    upload_max_path_depth: int = Field(default=8, gt=0)
    upload_chunk_bytes: int = Field(default=1024 * 1024, gt=0)
    max_active_runs: int = Field(default=2, gt=0)

    # TODO: Supabase vs local Docker. Both work through database_url with repository=postgres.
    repository: Literal["memory", "postgres"] = "memory"
    database_url: SecretStr | None = None
    db_write_attempts: int = Field(default=3, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
