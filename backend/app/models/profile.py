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


class DecisionAction(StrEnum):
    """What the advisor decided for one field or list row (step 8)."""

    CHOOSE = "choose"  # scalar: take one candidate group
    VALUE = "value"  # scalar: typed value
    LEAVE_EMPTY = "leave_empty"  # scalar: no value (Merkmale stay unbekannt)
    ACCEPT = "accept"  # list row without conflict: keep as is
    EDIT = "edit"  # list row: choose or type single cells
    DROP = "drop"  # list row: the row was wrong, do not write it


class AppliedDecision(BaseModel):
    """An advisor decision applied to a draft field; set only on the confirmed draft."""

    action: DecisionAction
    value: Any = None  # final scalar value or list row; None for leave_empty and drop
    group: int | None = None  # group whose sources back the value; None = entered by advisor
    typed: bool = False  # the advisor typed (part of) the value
    text: str  # German decision text for pruefhinweis.entscheidung (values and files only)


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
    decision: AppliedDecision | None = None  # advisor decision (confirmed draft only)

    @property
    def dropped(self) -> bool:
        """The advisor left the field empty or dropped the row."""
        return self.decision is not None and self.decision.action in (
            DecisionAction.LEAVE_EMPTY,
            DecisionAction.DROP,
        )

    @property
    def chosen(self) -> Any:
        """Value to use: the advisor's decision, else the agreed value, else the
        suggestion for a conflict (unconfirmed publish only)."""
        if self.decision is not None:
            return self.decision.value
        if self.status is FieldStatus.CONFLICT and self.suggestion is not None:
            return self.groups[self.suggestion].value
        return self.value

    @property
    def chosen_group(self) -> CandidateGroup | None:
        """Group whose sources back the value; None when the advisor entered it."""
        if self.decision is not None:
            return self.groups[self.decision.group] if self.decision.group is not None else None
        if self.status is FieldStatus.CONFLICT and self.suggestion is not None:
            return self.groups[self.suggestion]
        return self.groups[0] if self.groups else None

    @property
    def entered_by_advisor(self) -> bool:
        """Value without a document source: the advisor is the source."""
        return (
            self.decision is not None
            and self.decision.group is None
            and self.decision.action in (DecisionAction.VALUE, DecisionAction.EDIT)
        )


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
