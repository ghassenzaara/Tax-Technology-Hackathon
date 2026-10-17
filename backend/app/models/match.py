"""Steps 13 to 15: company features, scored pairs, the judge's verdict and stored matches."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from app.models.features import EmbeddingRecord
from app.models.law import Law, LawSection


class Direction(StrEnum):
    RISIKO = "risiko"
    CHANCE = "chance"
    NEUTRAL = "neutral"


class Trigger(StrEnum):
    LAW = "law"  # a new law was ingested
    PROFIL_UPDATE = "profil_update"  # the advisor changed a confirmed profile


class Invalidation(StrEnum):
    """Why a stored decision is no longer current. Final reasons obsolete open alerts;
    pending reasons wait for the next explicit screening (the alert keeps its status but
    cannot be sent)."""

    NOT_CANDIDATE = "not_candidate"  # final: feature gone, rule excluded, below threshold
    PROFILE_CHANGED = "profile_changed"  # pending: a non-contact profile edit
    DEFERRED = "deferred"  # pending: over the judge budget of one execution
    JUDGE_FAILED = "judge_failed"  # pending: the judge output stayed invalid
    MIGRATED = "migrated"  # pending: stored before fingerprints existed


FINAL_INVALIDATIONS = frozenset({Invalidation.NOT_CANDIDATE})


class CompanyFeatures(BaseModel):
    """Input of matching: the company's feature vectors plus its legal form."""

    company_id: str
    gesellschaft_id: int | None = None
    rechtsform: str | None = None
    features: list[EmbeddingRecord] = []


class ScoredFeature(BaseModel):
    feature: EmbeddingRecord
    score: float
    norm_match: bool = False


class SectionScore(BaseModel):
    """Stage 2 result for one section: its best features, highest score first."""

    law: Law
    section: LawSection
    features: list[ScoredFeature]
    form_unknown: bool = False
    passed: bool = False  # above the threshold and within the candidate cap

    @property
    def score(self) -> float:
        return self.features[0].score

    @property
    def norm_match(self) -> bool:
        return any(f.norm_match for f in self.features)


class JudgeVerdict(BaseModel):
    """Output of the judge call (strict json_schema)."""

    relevant: bool
    direction: Direction
    reason: str
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _reason_when_relevant(self) -> "JudgeVerdict":
        if self.relevant and not self.reason.strip():
            raise ValueError("reason must not be empty when relevant is true")
        return self


class Match(BaseModel):
    """A row of `screening.match`: one judged pair per company and law section."""

    id: int | None = None
    company_id: str
    gesellschaft_id: int | None = None
    law_id: int
    law_section_id: int
    objekt_typ: str
    objekt_id: int
    feature_text: str
    score: float
    norm_match: bool = False
    form_unknown: bool = False
    judge_relevant: bool
    judge_direction: Direction | None = None
    judge_reason: str
    judge_confidence: float | None = None
    model: str
    trigger: Trigger = Trigger.LAW
    profile_version: int | None = None
    created_at: datetime | None = None
    is_current: bool = True  # false: the inputs changed since the verdict (kept as history)
    input_fingerprint: str | None = None  # hash of every judge input of this section
    invalidation_reason: Invalidation | None = None
    # Filled by list reads, for printing.
    section_ref: str | None = None
    law_title: str | None = None

    @property
    def feature_ref(self) -> str:
        return f"{self.objekt_typ}:{self.objekt_id}"


class ScreenReport(BaseModel):
    company_id: str
    dry_run: bool = False
    trigger: Trigger = Trigger.LAW
    features: int = 0
    sections: int = 0
    removed: dict[str, int] = {}  # stage 1: pairs removed per rule
    pairs: int = 0  # stage 1: surviving (feature, section) pairs
    scored: list[SectionScore] = []  # stage 2, highest score first
    candidates: int = 0  # sections sent to stage 3
    skipped: int = 0  # verdict reused: the judge inputs are unchanged (fingerprint)
    judged: int = 0
    hits: int = 0
    stored: int = 0
    unjudged: list[str] = []  # section refs whose judge output stayed invalid
    deferred: list[str] = []  # over the judge budget; the next screening request continues
    invalidated: int = 0  # earlier decisions made non-current by this reconciliation
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    status: Literal["screened", "refused", "failed", "nothing_to_do"] = "screened"
    message: str | None = None

    @property
    def complete(self) -> bool:
        """Every candidate section has a current decision (nothing deferred or failed)."""
        if self.status == "failed":
            return False
        return self.status != "screened" or self.dry_run or not (self.unjudged or self.deferred)

    def summary(self) -> dict:
        """JSON for job results: no vectors; stage 2 as one line per section."""
        data = self.model_dump(mode="json", exclude={"scored"})
        data["complete"] = self.complete
        data["scored"] = [
            {
                "law_id": s.law.id,
                "law_section_id": s.section.id,
                "section_ref": s.section.section_ref,
                "score": round(s.score, 4),
                "norm_match": s.norm_match,
                "passed": s.passed,
                "best_feature": s.features[0].feature.ref,
            }
            for s in self.scored
        ]
        return data

    def headline(self) -> str:
        return (
            f"{self.candidates} candidate sections, {self.hits} hits in {self.seconds:.1f} seconds"
        )


class ScreeningChanges(BaseModel):
    """One screening execution's result, applied in one transaction with the alerts."""

    company_id: str
    law_ids: list[int]  # the reconciled scope
    profile_version: int | None = None
    judged: list[Match] = []  # new verdicts (current)
    reused: list[int] = []  # law section ids whose verdict is reused (made current)
    invalidated: dict[int, Invalidation] = {}  # law section id -> reason
    coverage: dict[int, tuple[int, int]] = {}  # law id -> (deferred, unjudged)
    screened_entries: list[int] = []  # profile_changed audit entries now re-screened


class ScreeningStatus(BaseModel):
    """Is screening of the company up to date? Shown in Progress."""

    pending_changes: int = 0  # profile edits not re-screened yet
    incomplete_laws: list[int] = []  # deferred or failed sections remain
    unscreened_laws: list[int] = []  # stored laws never screened for the company
    stale_decisions: int = 0  # non-current decisions waiting for screening

    @computed_field
    @property
    def pending(self) -> bool:
        return bool(
            self.pending_changes
            or self.incomplete_laws
            or self.unscreened_laws
            or self.stale_decisions
        )
