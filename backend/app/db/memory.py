"""In-memory repository: the default until a database is chosen, and used by most tests.

Stores deep copies, so callers cannot change stored rows by accident (like a real DB).
Publishing only records the draft; the profile tables exist in Postgres only.
"""

from app.config import Settings
from app.db.repository import PublishReport, Repository
from app.models.extraction import Candidate, DocumentExtraction
from app.models.profile import ProfileDraft
from app.profile.schema import ProfileSchema


class MemoryRepository(Repository):
    def __init__(self) -> None:
        self.candidates: dict[tuple[str, str], list[Candidate]] = {}  # (company, doc) -> rows
        self.extractions: dict[tuple[str, str], DocumentExtraction] = {}
        self.drafts: dict[str, list[ProfileDraft]] = {}
        self.published: dict[str, ProfileDraft] = {}

    async def initialize(self) -> None:
        pass

    async def replace_document_candidates(self, extraction: DocumentExtraction) -> None:
        key = (extraction.company_id, extraction.doc_id)
        record = extraction.model_copy(update={"candidates": []}, deep=True)
        previous = self.extractions.get(key)
        if extraction.status.has_result:
            self.candidates[key] = [c.model_copy(deep=True) for c in extraction.candidates]
        elif previous is not None:  # keep the earlier candidates and document metadata
            for name in ("document_class", "document_title", "document_date"):
                if getattr(record, name) is None:
                    setattr(record, name, getattr(previous, name))
        self.extractions[key] = record

    async def list_candidates(self, company_id: str) -> list[Candidate]:
        return [
            c.model_copy(deep=True)
            for (company, _), rows in sorted(self.candidates.items())
            if company == company_id
            for c in rows
        ]

    async def list_extractions(self, company_id: str) -> list[DocumentExtraction]:
        return [
            e.model_copy(deep=True)
            for (company, _), e in sorted(self.extractions.items())
            if company == company_id
        ]

    async def save_profile_draft(self, draft: ProfileDraft) -> None:
        self.drafts.setdefault(draft.company_id, []).append(draft.model_copy(deep=True))

    async def get_profile_draft(self, company_id: str) -> ProfileDraft | None:
        drafts = self.drafts.get(company_id)
        return drafts[-1].model_copy(deep=True) if drafts else None

    async def publish_profile(
        self,
        draft: ProfileDraft,
        extractions: list[DocumentExtraction],
        schema: ProfileSchema,
        settings: Settings,
    ) -> PublishReport:
        self.published[draft.company_id] = draft.model_copy(deep=True)
        return PublishReport(project=draft.company_id)
