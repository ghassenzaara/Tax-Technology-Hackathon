"""Repository interface and factory. Supabase vs local Docker is a settings change only.

Two schemas: `pipeline` (our working data: candidates, extraction records, drafts, project
state, runs of the upload layer) and the teammate's `profil` (backend/db_scheme), which
receives the published draft, the advisor's confirmation (step 8) and the feature vectors
(step 9). Step 10 deletes the company's working rows in `pipeline` (including the per-file
run progress) and clears the upload folder of its runs; `profil`, publications, the project
state and the runs as history stay. Laws, their sections and the screening matches (steps 11
to 15) live in schema `screening` and are never deleted by step 10.
"""

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from app.config import Settings
from app.models.audit import AuditEntry, AuditFilter
from app.models.extraction import Candidate, DocumentExtraction
from app.models.features import EmbeddingRecord, ProfileSnapshot
from app.models.law import Law, LawSection
from app.models.match import CompanyFeatures, Match
from app.models.profile import ProfileDraft
from app.models.project import Confirmation, ProjectStatus
from app.models.review import HintRow
from app.models.run import Run, RunDocument
from app.models.update import ConfirmedProfile, UpdateRequest, UpdateResult
from app.profile.schema import ProfileSchema

WORKING_TABLES = ("field_candidates", "document_extractions", "profile_drafts", "run_documents")


class RepositoryError(RuntimeError):
    pass


class NotFoundError(RepositoryError):
    """Unknown company or run."""


class RunActiveError(RepositoryError):
    """A run of the company is queued or running; a second one is refused."""


class UnsupportedError(RepositoryError):
    """The configured repository cannot do this (for example the memory repository)."""


class PublishError(RepositoryError):
    """The draft cannot become a profile (for example a required column has no value)."""


class StateError(RepositoryError):
    """The project is not in the state the step needs (for example confirm before publish)."""


class StaleDraftError(RepositoryError):
    """The draft changed since the advisor loaded it; the UI must reload."""


class StaleProfileError(RepositoryError):
    """The confirmed profile changed (new version) since the advisor loaded it."""


class BusyError(RepositoryError):
    """Another step (confirm, embed, cleanup) of the same project is running."""


class DuplicateLawError(RepositoryError):
    """A law with the same file content is already stored (use --replace)."""


class PublishReport(BaseModel):
    project: str
    rows: dict[str, int] = {}  # table -> rows written
    hints: int = 0  # pruefhinweis rows (conflicts, skipped rows)
    hints_closed: int = 0  # of those, closed by an advisor decision (status geklaert)
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
        """Write the draft into the profile schema as unconfirmed rows and set the project
        state to published. Replaces an earlier publish of the same project, but only in
        state published: a confirmed profile is never overwritten (StateError)."""

    # Steps 8 to 10 -------------------------------------------------------------------

    @abstractmethod
    async def get_project_state(self, company_id: str) -> ProjectStatus | None: ...

    @abstractmethod
    async def record_error(self, company_id: str, error: str) -> None:
        """Store the last error of a failed step; the state stays unchanged."""

    @abstractmethod
    async def begin_step(self, company_id: str, step: str, timeout_s: float) -> None:
        """Mark the project busy with `step` (BusyError when another step runs). A marker
        older than `timeout_s` counts as left over from a crash and is taken over. No-op
        for a project without state row (the step's own state check refuses it)."""

    @abstractmethod
    async def end_step(self, company_id: str) -> None: ...

    @abstractmethod
    async def list_open_hints(self, company_id: str) -> list[HintRow]:
        """Open pruefhinweis rows of the company's target company."""

    @abstractmethod
    async def confirm_profile(
        self,
        draft: ProfileDraft,
        extractions: list[DocumentExtraction],
        schema: ProfileSchema,
        settings: Settings,
        confirmation: Confirmation,
    ) -> PublishReport:
        """Step 8 in one transaction: state must be published (StateError) and the latest
        draft must have `confirmation.draft_version` (StaleDraftError); then republish the
        confirmed draft with every row stamped, close decided hints, set state confirmed.
        Any error rolls everything back."""

    @abstractmethod
    async def load_profile_snapshot(
        self, company_id: str, schema: ProfileSchema
    ) -> ProfileSnapshot:
        """The confirmed profile rows step 9 builds features from (never the draft)."""

    @abstractmethod
    async def replace_embeddings(
        self,
        company_id: str,
        model: str,
        records: list[EmbeddingRecord],
        fallback: int,
    ) -> None:
        """Step 9 in one transaction: state must be confirmed, embedded or cleaned (a refresh
        after an update); replace the company's vectors of `model`, check the stored set
        equals `records`, store the counts, clear the stale flag and move confirmed to
        embedded (cleaned stays cleaned). Any error rolls everything back."""

    @abstractmethod
    async def list_embedding_keys(self, company_id: str, model: str) -> set[str]:
        """Stored vectors of the company as "objekt_typ:objekt_id"."""

    @abstractmethod
    async def list_embeddings(self, company_id: str, model: str) -> list[EmbeddingRecord]:
        """Stored vectors with their sentence and metadata (for the refresh diff)."""

    # Plan section 4b: updates after confirmation (Postgres only) -------------------------

    async def update_profile(
        self, request: UpdateRequest, schema: ProfileSchema, settings: Settings, today: date
    ) -> UpdateResult:
        """Apply the advisor's changes in one transaction (see db/profil_updater.py)."""
        raise UnsupportedError("profile updates need REPOSITORY=postgres")

    async def read_profile(
        self, company_id: str, schema: ProfileSchema, settings: Settings
    ) -> ConfirmedProfile:
        """Confirmed values with citations, profile version and history."""
        raise UnsupportedError("the confirmed profile needs REPOSITORY=postgres")

    @abstractmethod
    async def list_audit(self, audit_filter: AuditFilter) -> list[AuditEntry]:
        """Audit log entries matching every given filter, newest first."""

    @abstractmethod
    async def delete_working_data(self, company_id: str) -> dict[str, int]:
        """Step 10: delete the company's rows in the pipeline working tables and clear the
        upload folder of its runs (one transaction). Returns rows deleted per table."""

    @abstractmethod
    async def count_working_rows(self, company_id: str) -> dict[str, int]:
        """Rows left per pipeline working table, for the cleanup verification."""

    @abstractmethod
    async def mark_cleaned(self, company_id: str) -> None:
        """Move the project from embedded to cleaned (StateError otherwise)."""

    # Step 0: runs of the upload layer -----------------------------------------------------

    @abstractmethod
    async def ping(self) -> None:
        """Raise when the database cannot be reached (health check)."""

    @abstractmethod
    async def save_run(self, run: Run) -> None:
        """Insert or replace the run row."""

    @abstractmethod
    async def get_run(self, run_id: str) -> Run | None: ...

    @abstractmethod
    async def list_runs(self, company_id: str | None = None) -> list[Run]:
        """Runs, newest first; of one company or of all."""

    @abstractmethod
    async def save_run_documents(self, documents: list[RunDocument]) -> None:
        """Insert or replace progress rows (key: run id and relative path)."""

    @abstractmethod
    async def list_run_documents(self, run_id: str) -> list[RunDocument]:
        """Progress rows of a run, ordered by relative path."""

    @abstractmethod
    async def interrupt_active_runs(self, error: str) -> int:
        """Mark every queued or running run interrupted (server start). Returns the count."""

    @abstractmethod
    async def list_project_states(self) -> list[ProjectStatus]: ...

    @abstractmethod
    async def company_exists(self, company_id: str) -> bool:
        """Is the id used anywhere: project state, runs, working data, or a deal in profil
        (seeds and hand-made profiles included)? A new company id must not be."""

    # Steps 11 to 15: laws and screening -------------------------------------------------

    @abstractmethod
    async def list_norm_codes(self) -> set[str]:
        """Norm codes of the vocabulary (profil.norm); empty when the schema is missing."""

    @abstractmethod
    async def find_law(self, content_hash: str) -> Law | None: ...

    @abstractmethod
    async def save_law(self, law: Law, sections: list[LawSection], *, replace: bool) -> int:
        """Write the law and all its sections in one transaction; returns the law id. A law
        with the same content hash is refused (DuplicateLawError) unless `replace`, which
        deletes it first (its sections and matches by cascade)."""

    @abstractmethod
    async def list_laws(self) -> list[Law]:
        """Stored laws with their section count, oldest first."""

    @abstractmethod
    async def update_law_change_summary(self, law_id: int, change_summary: str | None) -> None:
        """Refresh source-level UI text without replacing sections, vectors or matches."""

    @abstractmethod
    async def list_law_sections(self, law_id: int | None = None) -> list[LawSection]:
        """Sections with vectors, of one law or of all."""

    @abstractmethod
    async def company_features(self, company_id: str) -> CompanyFeatures:
        """The company's feature vectors (every model) and its legal form. Read-only."""

    @abstractmethod
    async def list_matches(self, company_id: str, only_hits: bool = False) -> list[Match]:
        """Stored matches, hits first, then by score."""

    @abstractmethod
    async def save_matches(self, matches: list[Match]) -> None:
        """Insert or replace matches (key: company and law section) in one transaction."""

    @abstractmethod
    async def pending_profile_changes(self, company_id: str) -> list[int]:
        """Ids of profile_changed audit entries that matching has not re-screened yet."""

    @abstractmethod
    async def mark_screened(self, entry_ids: list[int]) -> None:
        """Set `screened_at` on those audit entries (the only allowed audit update)."""

    async def initialize_profile_schema(
        self, sql_dir: Path, *, reset: bool = False, seed: bool = False
    ) -> list[str]:
        raise RepositoryError("the profile schema needs REPOSITORY=postgres")

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass


def refused_publish(company_id: str, state: str) -> StateError:
    """A confirmed profile is never overwritten by a rerun."""
    # TODO: decide how a later DD updates or reopens a confirmed profile (new deal,
    # versioned profile, or reopen); out of scope for now.
    return StateError(
        f"project '{company_id}' is already {state}: a confirmed profile is never "
        "overwritten by a rerun (updating a confirmed profile is not supported yet; "
        "use another --company-id)"
    )


def make_repository(settings: Settings) -> Repository:
    if settings.repository == "postgres":
        if settings.database_url is None:
            raise RepositoryError("REPOSITORY=postgres needs DATABASE_URL")
        from app.db.postgres import PostgresRepository  # driver import stays optional

        return PostgresRepository(settings.database_url.get_secret_value())
    from app.db.memory import MemoryRepository

    return MemoryRepository()
