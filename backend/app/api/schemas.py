"""Request and response bodies of the HTTP API that are not service models already.

Profile fields never appear here: drafts and profiles carry whatever the schema file and
the stored draft hold. No body has a person field (single-user platform); the server stamps
the configured advisor label.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.audit import AuditEntry
from app.models.job import JobView
from app.models.law import Law
from app.models.match import Match, ScreeningStatus
from app.models.outreach import Outreach
from app.models.project import ProjectState
from app.models.review import Decision
from app.models.run import RunState
from app.models.update import Change
from app.runs.upload import AcceptedFile, RejectedFile


class Health(BaseModel):
    status: str  # ok
    database: str  # ok


class UploadResponse(BaseModel):
    company_id: str  # opaque; send it on every later call
    run_id: str
    project_name: str | None = None
    accepted: list[AcceptedFile]
    rejected: list[RejectedFile]


class RunView(BaseModel):
    run_id: str
    state: RunState
    started_by: str  # upload | rerun
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    counts: dict[str, int] = {}  # documents per extraction status
    published: bool = False  # the run wrote a draft into the profile (state published)
    last_error: str | None = None


class DocumentProgress(BaseModel):
    relative_path: str
    parse_status: str  # pending | ok | partial | failed | unsupported
    extraction_status: str  # pending | ok | partial | failed | skipped | write_failed
    pages: int | None = None
    warnings: list[str] = []
    seconds: float | None = None


class ProjectView(BaseModel):
    state: ProjectState | None = None  # None: nothing published yet
    profile_version: int | None = None
    embeddings_stale: bool = False
    running_step: str | None = None  # confirm | embed | cleanup while it runs
    last_error: str | None = None  # last failed step (confirm, embed, cleanup)
    published_at: datetime | None = None
    confirmed_at: datetime | None = None
    embedded_at: datetime | None = None
    cleaned_at: datetime | None = None


class RecoveryView(BaseModel):
    available: bool = False  # the recover call would start work
    action: str | None = None  # embed_and_cleanup | rebuild_vectors | cleanup | screen_pending
    reason: str = ""


class ProgressResponse(BaseModel):
    company_id: str
    project: ProjectView
    run: RunView | None = None  # the latest run
    documents: list[DocumentProgress] = []  # of the latest run
    active_job: JobView | None = None  # advance, refresh or screening of the company
    latest_job: JobView | None = None  # the newest job of the company (its result or error)
    recovery: RecoveryView = RecoveryView()
    screening: ScreeningStatus | None = None  # after embedding: is screening up to date?


class ConfirmBody(BaseModel):
    """The advisor's final choice per open decision (see the Draft call)."""

    draft_version: str  # from the Draft call; a newer draft answers stale_draft
    decisions: list[Decision] = []


class UpdateBody(BaseModel):
    """Changes to a confirmed profile (edit, add, remove, end)."""

    profile_version: int  # from the Profile call; a newer version answers stale_profile
    reason: str | None = None
    changes: list[Change] = Field(min_length=1)


class CompanySummary(BaseModel):
    company_id: str
    project_name: str | None = None
    state: ProjectState | None = None
    run_state: RunState | None = None  # of the latest run
    embeddings_stale: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CompanyList(BaseModel):
    companies: list[CompanySummary]


class AuditList(BaseModel):
    entries: list[AuditEntry]  # newest first


class LawList(BaseModel):
    laws: list[Law]  # oldest first, with section counts


class SectionView(BaseModel):
    """A law section without its vector."""

    id: int
    nr: int
    section_ref: str
    heading: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    text: str
    norms: list[str] = []
    tax_types: list[str] = []
    model: str


class LawDetail(BaseModel):
    law: Law  # metadata, summaries and section count
    sections: list[SectionView]  # in law order


class ScreeningBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str | None = None  # None: every eligible company
    law_id: int | None = None  # None: every stored law
    force: bool = False  # judge again even when the inputs are unchanged
    dry_run: bool = False  # stages 1 and 2 only; nothing changes, no LLM call


class MatchList(BaseModel):
    matches: list[Match]  # hits first, then by score


class RecoveryResponse(BaseModel):
    action: str | None = None
    reason: str
    job: JobView | None = None  # None: nothing to recover (200)


class DismissBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class TemplateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    body: str


class SendBody(BaseModel):
    """The final values from the UI; the backend sends exactly these."""

    model_config = ConfigDict(extra="forbid")

    recipient: str
    subject: str
    body: str
    confirmed: Literal[True]  # the advisor confirmed this exact email
    template_updated_at: datetime | None = None  # from the preview


class OutreachList(BaseModel):
    outreach: list[Outreach]  # newest first
