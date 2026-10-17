"""Step 9: profile rows read for embedding, the features built from them, and the result.

A feature is one profile object with its own vector: a `ja` Merkmal, a finding or an
appeal (`profil.embedding.objekt_typ`). Numbers stay in the structured metadata
(steuerarten, normen, jahre, offen), never in the embedded sentence.
"""

from pydantic import BaseModel


class NormRef(BaseModel):
    code: str  # profil.norm.code, for example '§ 8c KStG'
    gesetz: str  # statute, for example 'KStG'
    bezeichnung: str | None = None


class MerkmalRow(BaseModel):
    id: int
    code: str  # merkmal_def.code, for example 'B07'
    wert: str  # ja | nein | unbekannt
    bezeichnung: str | None = None  # merkmal_def.bezeichnung
    kurztext: str | None = None
    normen: list[NormRef] = []


class FindingRow(BaseModel):
    id: int
    titel: str
    kurzsachverhalt: str | None = None
    richtung: str  # risiko | chance | beides
    steuerarten: list[str] = []
    jahr_von: int | None = None
    jahr_bis: int | None = None
    normen: list[NormRef] = []


class AppealRow(BaseModel):
    id: int
    art: str  # einspruch | klage
    status: str  # anhaengig | ruht | erledigt
    streitpunkt: str | None = None
    ruht_rechtsgrundlage: str | None = None
    jahr: int | None = None  # year of the contested assessment
    steuerart: str | None = None


class AssessmentRow(BaseModel):
    """A `veranlagung` row: the basis of the `offen` flag (same rule as v_offene_jahre)."""

    jahr: int
    steuerart: str
    stand: str
    vorbehalt_164: str
    vorlaeufig_165: bool = False
    ablaufhemmung: str | None = None
    verjaehrt: bool = False


class ProfileSnapshot(BaseModel):
    company_id: str
    gesellschaft_id: int
    names: list[str] = []  # company, party and file names that must never be embedded
    merkmale: list[MerkmalRow] = []
    findings: list[FindingRow] = []
    appeals: list[AppealRow] = []
    assessments: list[AssessmentRow] = []


class Feature(BaseModel):
    """One object to embed, with the facts the sentence may use and its filter metadata."""

    objekt_typ: str  # merkmal_wert | finding | rechtsbehelf
    objekt_id: int
    gesellschaft_id: int
    facts: dict[str, str]  # prompt input for the sentence (may contain numbers and names)
    fallback: str  # deterministic digit-free sentence used when the LLM output fails
    forbidden: list[str] = []  # names that must not appear in the sentence
    steuerarten: list[str] = []
    normen: list[str] = []
    jahre: tuple[int | None, int | None] | None = None  # inclusive; None bound = open-ended
    offen: bool | None = None  # can the affected year still be changed?

    @property
    def ref(self) -> str:
        return f"{self.objekt_typ}:{self.objekt_id}"


class EmbeddingRecord(BaseModel):
    """One row of `profil.embedding`."""

    objekt_typ: str
    objekt_id: int
    gesellschaft_id: int
    steuerarten: list[str] = []
    normen: list[str] = []
    jahre: tuple[int | None, int | None] | None = None
    offen: bool | None = None
    modell: str
    vektor: list[float]
    text_basis: str  # the descriptive sentence (digit-free, no names)

    @property
    def ref(self) -> str:
        return f"{self.objekt_typ}:{self.objekt_id}"


class EmbedReport(BaseModel):
    company_id: str
    model: str
    expected: int = 0
    written: int = 0
    reused: int = 0  # refresh: features that kept their vector and sentence
    fallback: int = 0  # features that got the deterministic fallback sentence
    fallback_features: list[str] = []
    seconds: float = 0.0
    sentences: dict[str, str] = {}  # feature ref -> sentence, for the run report

    def headline(self) -> str:
        return f"{self.written} features embedded in {self.seconds:.1f} seconds"
