"""Profile draft (step 7): the deterministic merge result the advisor reviews in step 8."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.extraction import Candidate, ExtractionStatus, utc_now


class FieldStatus(StrEnum):
    MISSING = "missing"  # no candidates
    SINGLE_SOURCE = "single_source"  # one value, supported by one document
    CONFIRMED = "confirmed"  # one value, supported by at least two distinct documents
    CONFLICT = "conflict"  # several different values; the advisor picks


class CandidateGroup(BaseModel):
    """Candidates that count as the same value. `value` is the most precise member
    (for list items: the most complete one, gaps filled from the other members)."""

    value: Any
    documents: list[str]  # distinct source_doc ids, sorted
    candidates: list[Candidate]
    rounding_equivalent: bool = False  # members differ only by rounding
    max_confidence: float = 0.0
    best_rank: int | None = None  # strongest source class rank (1 = Bescheid, 9 = Entwurf)


class FieldResult(BaseModel):
    name: str
    status: FieldStatus
    value: Any = None  # only set for confirmed and single_source
    groups: list[CandidateGroup] = []
    suggestion: int | None = None  # non-binding group index, only for conflicts
    rounding_equivalent: bool = False
    needs_review: bool = False  # every supporting candidate needs review
    intra_document_conflict: bool = False  # one document supports several groups
    key: dict[str, Any] | None = None  # list items: the identifying key values
    items: list["FieldResult"] = []  # list fields: one result per item key
    warnings: list[str] = []  # for example candidates dropped because the value is corrupt

    @property
    def chosen(self) -> Any:
        """Value to use: the agreed value, or the suggestion for a conflict."""
        if self.status is FieldStatus.CONFLICT and self.suggestion is not None:
            return self.groups[self.suggestion].value
        return self.value

    @property
    def chosen_group(self) -> CandidateGroup | None:
        if self.status is FieldStatus.CONFLICT and self.suggestion is not None:
            return self.groups[self.suggestion]
        return self.groups[0] if self.groups else None


class IncompleteDocument(BaseModel):
    doc_id: str
    filename: str
    status: ExtractionStatus
    reason: str


class ProfileDraft(BaseModel):
    company_id: str
    fields: list[FieldResult]
    counts: dict[FieldStatus, int]
    incomplete_documents: list[IncompleteDocument] = []
    db_incomplete: bool = False  # some candidates could not be written to the DB
    schema_fields: list[str]
    created_at: datetime = Field(default_factory=utc_now)

    def field(self, name: str) -> FieldResult:
        return next(f for f in self.fields if f.name == name)
