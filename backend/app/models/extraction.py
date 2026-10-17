"""Extraction results (steps 5 and 6): candidate rows and the per-document agent result.

Row models are generic: the profile schema decides field names and value types.
TODO: align with the teammate's DB schema once delivered (placeholder: `field_candidates`).
"""

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.document import TokenUsage


def utc_now() -> datetime:
    return datetime.now(UTC)


class Confidence(StrEnum):
    """What the model reports. Code maps it to a number (see `agents/verify.py`)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ExtractionStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"  # some chunks failed or some fields were dropped
    FAILED = "failed"  # no usable output, no candidates
    SKIPPED = "skipped"  # no agent started (failed, unsupported or empty document)
    WRITE_FAILED = "write_failed"  # extracted, but the DB write failed

    @property
    def has_result(self) -> bool:
        """Did this attempt produce output that may replace an earlier one?"""
        return self in (ExtractionStatus.OK, ExtractionStatus.PARTIAL)


class Candidate(BaseModel):
    """One value for one field from one document: a row of `field_candidates`."""

    company_id: str
    field_name: str
    value: Any  # JSON value as extracted (dates as ISO strings); a dict for list items
    evidence: str
    source_doc: str  # added by code, never by the model
    source_filename: str
    source_page: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)
    needs_review: bool = False
    chunk_index: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class DocumentExtraction(BaseModel):
    """Result of one agent run over one document; also a row of `document_extractions`."""

    company_id: str
    doc_id: str
    filename: str
    status: ExtractionStatus
    candidates: list[Candidate] = []
    warnings: list[str] = []
    agent_s: float = 0.0
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    chunks: int = 0
    repair_used: bool = False
    # Document metadata from the agent (first chunk); the class sets the source rank.
    document_class: str | None = None
    document_title: str | None = None
    document_date: str | None = None  # ISO date
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def fields_found(self) -> int:
        return len({c.field_name for c in self.candidates})


class ExtractionSummary(BaseModel):
    documents: int
    by_status: dict[ExtractionStatus, int]
    wall_s: float
    token_usage: TokenUsage
    candidates: int

    def headline(self) -> str:
        return f"{self.documents} documents extracted in {self.wall_s:.1f} seconds"


def summarize_extractions(
    extractions: Iterable[DocumentExtraction], wall_s: float
) -> ExtractionSummary:
    extractions = list(extractions)
    return ExtractionSummary(
        documents=len(extractions),
        by_status=dict(Counter(e.status for e in extractions)),
        wall_s=wall_s,
        token_usage=sum((e.token_usage for e in extractions), TokenUsage()),
        candidates=sum(len(e.candidates) for e in extractions),
    )
