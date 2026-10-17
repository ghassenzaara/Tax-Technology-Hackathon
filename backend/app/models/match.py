"""Steps 13 to 15: company features, scored pairs, the judge's verdict and stored matches."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.features import EmbeddingRecord
from app.models.law import Law, LawSection


class Direction(StrEnum):
    RISIKO = "risiko"
    CHANCE = "chance"
    NEUTRAL = "neutral"


class Trigger(StrEnum):
    LAW = "law"  # a new law was ingested
    PROFIL_UPDATE = "profil_update"  # the advisor changed a confirmed profile


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
    skipped: int = 0  # already judged with an unchanged feature
    judged: int = 0
    hits: int = 0
    stored: int = 0
    unjudged: list[str] = []  # section refs whose judge output stayed invalid
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    status: Literal["screened", "refused", "nothing_to_do"] = "screened"
    message: str | None = None

    def headline(self) -> str:
        return (
            f"{self.candidates} candidate sections, {self.hits} hits in {self.seconds:.1f} seconds"
        )
