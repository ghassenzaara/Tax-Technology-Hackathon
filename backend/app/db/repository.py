"""Repository interface and factory. Supabase vs local Docker is a settings change only.

Two schemas: `pipeline` (our working data: candidates, extraction records, drafts) and the
teammate's `profil` (backend/db_scheme), which receives the published draft.
TODO: step 10 adds deleting a company's working data (candidates, extraction records).
"""

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel

from app.config import Settings
from app.models.extraction import Candidate, DocumentExtraction
from app.models.profile import ProfileDraft
from app.profile.schema import ProfileSchema


class RepositoryError(RuntimeError):
    pass


class PublishError(RepositoryError):
    """The draft cannot become a profile (for example a required column has no value)."""


class PublishReport(BaseModel):
    project: str
    rows: dict[str, int] = {}  # table -> rows written
    hints: int = 0  # pruefhinweis rows (conflicts, skipped rows)
    skipped: list[str] = []


class Repository(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        """Create the pipeline tables if they do not exist."""

    @abstractmethod
    async def replace_document_candidates(self, extraction: DocumentExtraction) -> None:
        """Atomically replace one document's candidates and extraction record.

        Rerunning the same document (same doc_id) replaces instead of duplicating. Only an
        attempt with a result (ok, partial) replaces candidates: a failed or skipped
        attempt (for example an OpenAI outage) updates the status but keeps the earlier
        candidates and document class. A write failure leaves the previous rows intact.
        """

    @abstractmethod
    async def list_candidates(self, company_id: str) -> list[Candidate]: ...

    @abstractmethod
    async def list_extractions(self, company_id: str) -> list[DocumentExtraction]:
        """Stored extraction records without candidates (status, class, warnings)."""

    @abstractmethod
    async def save_profile_draft(self, draft: ProfileDraft) -> None: ...

    @abstractmethod
    async def get_profile_draft(self, company_id: str) -> ProfileDraft | None:
        """Latest saved draft for the company."""

    @abstractmethod
    async def publish_profile(
        self,
        draft: ProfileDraft,
        extractions: list[DocumentExtraction],
        schema: ProfileSchema,
        settings: Settings,
    ) -> PublishReport:
        """Write the draft into the profile schema as unconfirmed rows (bestaetigt_von
        stays empty until the advisor confirms, step 8). Replaces an earlier publish of
        the same project."""

    async def initialize_profile_schema(
        self, sql_dir: Path, *, reset: bool = False, seed: bool = False
    ) -> list[str]:
        raise RepositoryError("the profile schema needs REPOSITORY=postgres")

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass


def make_repository(settings: Settings) -> Repository:
    if settings.repository == "postgres":
        if settings.database_url is None:
            raise RepositoryError("REPOSITORY=postgres needs DATABASE_URL")
        from app.db.postgres import PostgresRepository  # driver import stays optional

        return PostgresRepository(settings.database_url.get_secret_value())
    from app.db.memory import MemoryRepository

    return MemoryRepository()
