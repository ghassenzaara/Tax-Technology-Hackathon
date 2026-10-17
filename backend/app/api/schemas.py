"""Request and response bodies of the HTTP API that are not service models already.

Profile fields never appear here: drafts and profiles carry whatever the schema file and
the stored draft hold. No body has a person field (single-user platform); the server stamps
the configured advisor label.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.audit import AuditEntry
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


class ProgressResponse(BaseModel):
    company_id: str
    project: ProjectView
    run: RunView | None = None  # the latest run
    documents: list[DocumentProgress] = []  # of the latest run


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
