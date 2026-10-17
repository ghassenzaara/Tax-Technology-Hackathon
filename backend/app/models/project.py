"""Project state shared by steps 8 to 10 (row of `pipeline.project_state`)."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel


class ProjectState(StrEnum):
    """Moves forward one step at a time; a failed step leaves it unchanged."""

    PUBLISHED = "published"  # draft written to profil as unconfirmed rows (step 7 + publish)
    CONFIRMED = "confirmed"  # advisor confirmed the profile (step 8)
    EMBEDDED = "embedded"  # every feature has a vector (step 9)
    CLEANED = "cleaned"  # working data deleted (step 10)


# States in which vectors may be (re)written: first embed, rerun, refresh after an update.
EMBEDDABLE = frozenset({ProjectState.CONFIRMED, ProjectState.EMBEDDED, ProjectState.CLEANED})


class ProjectStatus(BaseModel):
    company_id: str
    state: ProjectState
    published_at: datetime | None = None
    published_docs: list[str] = []  # doc_ids the last publish used (dokument rows)
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    draft_version: str | None = None
    embedded_at: datetime | None = None
    embed_model: str | None = None
    embeddings_expected: int | None = None
    embeddings_written: int | None = None
    embeddings_fallback: int | None = None
    expected_features: list[str] = []  # "objekt_typ:objekt_id" of every expected vector
    cleaned_at: datetime | None = None
    last_error: str | None = None
    profile_version: int | None = None  # 1 at confirmation, +1 per advisor update
    embeddings_stale: bool = False  # an update changed the profile, vectors not refreshed yet
    running_step: str | None = None  # confirm | embed | cleanup while it runs (busy marker)
    running_since: datetime | None = None


class Confirmation(BaseModel):
    """Stamp written on every profile row when the advisor confirms (step 8)."""

    by: str  # TODO: real identity of the confirming advisor once login exists
    on: date
    draft_version: str
    counts: dict[str, int] = {}  # decision counts for the profile_confirmed audit entry
