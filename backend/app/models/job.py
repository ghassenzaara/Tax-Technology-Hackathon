"""Background jobs of the HTTP API (rows of `pipeline.jobs`): law ingestion, screening, and
the post-confirmation work (advance: embed then clean up; refresh after an update).

A job is persisted before it is scheduled and never resumed automatically: after a restart
it is `interrupted` and the UI retries it explicitly (a new attempt linked by `retry_of`).
Email sending is never a job. `private` holds server-side inputs (for example the stored
law PDF) and never leaves the server.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.extraction import utc_now


class JobOperation(StrEnum):
    LAW_INGEST = "law_ingest"
    SCREEN = "screen"
    ADVANCE = "advance"  # state-selected: embed, clean up, pending screening
    REFRESH = "refresh"  # embedding refresh after an update, then configured screening


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"  # server stopped or restarted while the job was active

    @property
    def active(self) -> bool:
        return self in (JobStatus.QUEUED, JobStatus.RUNNING)

    @property
    def retryable(self) -> bool:
        return self in (JobStatus.FAILED, JobStatus.INTERRUPTED)


class JobView(BaseModel):
    """What the API returns: no server paths, credentials or vectors."""

    id: str
    operation: JobOperation
    company_id: str | None = None
    status: JobStatus = JobStatus.QUEUED
    params: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    error: str | None = None  # sanitized
    retry_of: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Job(JobView):
    dedupe_key: str  # one active job per key (company, or law content)
    private: dict[str, Any] = {}  # server-side inputs, never in a response

    def view(self) -> JobView:
        return JobView.model_validate(self.model_dump(exclude={"dedupe_key", "private"}))
