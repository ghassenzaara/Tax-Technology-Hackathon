"""Steps 11 and 12: a law (one ingested PDF), its sections and the ingest report."""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TaxType = Literal["KSt", "GewSt", "USt", "GrESt", "ESt", "LSt"]  # profil.steuerart


class LawKind(StrEnum):
    GESETZ = "gesetz"
    GESETZENTWURF = "gesetzentwurf"
    BMF_SCHREIBEN = "bmf_schreiben"
    VERORDNUNG = "verordnung"
    OTHER = "other"


class LawStatus(StrEnum):
    IN_KRAFT = "in_kraft"
    ENTWURF = "entwurf"
    ANGEKUENDIGT = "angekuendigt"


class LawMetadata(BaseModel):
    """Output of the one metadata call; missing values are null or empty, never guessed."""

    kind: LawKind
    title: str = Field(min_length=1)
    reference: str | None = None
    published_at: date | None = None
    effective_at: date | None = None
    status: LawStatus
    legal_forms: list[str] = []  # only when the law restricts itself to some legal forms
    tax_types: list[TaxType] = []
    summary: str | None = None
    change_summary: str | None = None  # UI: Was ändert sich? (a clarification for rulings)

    @field_validator("change_summary")
    @classmethod
    def _nonempty_change_summary(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("change_summary must be non-empty or null")
        return value


class Law(LawMetadata):
    """A row of `screening.law`."""

    id: int | None = None
    filename: str
    content_hash: str
    ingested_at: datetime | None = None
    sections: int = 0  # number of sections (filled by list reads)


class LawSection(BaseModel):
    """A row of `screening.law_section`: law text with its own vector."""

    id: int | None = None
    law_id: int | None = None
    nr: int
    section_ref: str
    heading: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    text: str
    norms: list[str] = []
    tax_types: list[str] = []
    model: str = ""
    vector: list[float] = []

    def embedding_text(self) -> str:
        """Heading plus section text (the law text itself, digits allowed)."""
        if self.heading and not self.text.startswith(self.heading):
            return f"{self.heading}\n{self.text}"
        return self.text


class IngestReport(BaseModel):
    law_id: int
    title: str
    kind: LawKind
    status: LawStatus
    published_at: date | None = None
    effective_at: date | None = None
    summary: str | None = None
    change_summary: str | None = None
    strategy: Literal["headings", "pages"]
    sections: int
    norms_found: list[str] = []
    norms_unmatched: list[str] = []  # not in profil.norm: candidates to extend the vocabulary
    dropped: list[str] = []  # boilerplate left out (table of contents, signature, footers)
    replaced: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0

    def headline(self) -> str:
        return f"{self.sections} sections ingested in {self.seconds:.1f} seconds"
