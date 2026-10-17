"""Runs of the upload layer (step 0): one execution of steps 1 to 7 plus publish, and one
progress record per discovered file (rows of `pipeline.runs` and `pipeline.run_documents`).
File names, counts and warnings only, never document text."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.extraction import utc_now

PENDING = "pending"  # parse or extraction status before the file is processed


class RunState(StrEnum):
    QUEUED = "queued"  # waiting for a slot under the global run cap
    RUNNING = "running"
    FINISHED = "finished"  # every document has a final status (single failures included)
    FAILED = "failed"  # the run itself crashed or the publish failed
    INTERRUPTED = "interrupted"  # server stopped or restarted while the run was active

    @property
    def active(self) -> bool:
        return self in (RunState.QUEUED, RunState.RUNNING)


class Run(BaseModel):
    run_id: str
    company_id: str
    project_name: str | None = None
    state: RunState = RunState.QUEUED
    folder: str | None = None  # upload folder; cleared by step 10
    started_by: str  # upload | rerun
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    counts: dict[str, int] = {}  # documents per final extraction status
    published: bool = False
    last_error: str | None = None


class RunDocument(BaseModel):
    run_id: str
    company_id: str
    relative_path: str
    doc_id: str | None = None
    parse_status: str = PENDING  # pending, then DocStatus: ok | partial | failed | unsupported
    # pending, then ExtractionStatus: ok | partial | failed | skipped | write_failed
    extraction_status: str = PENDING
    warnings: list[str] = []
    pages: int | None = None
    seconds: float | None = None  # parse time plus agent time
    input_tokens: int = 0
    output_tokens: int = 0
