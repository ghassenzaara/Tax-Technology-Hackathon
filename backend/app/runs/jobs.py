"""Background jobs of the HTTP API (law ingestion, screening, advance, refresh).

A job is persisted (`queued`) before its task is scheduled, so every accepted request is
visible to polling and survives a crash as a record. One active job per company (or per law
content, or one screening of all companies): a conflicting submission is refused with
BusyError and launches nothing. The services underneath take the existing per-company step
lock, so CLI work and updates are serialized with jobs too.

After a stop or restart, unfinished jobs are marked `interrupted` and abandoned step markers
are cleared (single-server deployment); nothing is resumed and no LLM is called at startup.
The UI retries a failed or interrupted job explicitly (a new attempt linked by `retry_of`,
which re-validates the current state when it runs) or asks for recovery, which selects the
work from the project state. Email is never a job: no retry can resend an email.
"""

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import Settings
from app.db.repository import BusyError, DuplicateLawError, NotFoundError, Repository, StateError
from app.models.job import Job, JobOperation, JobStatus, JobView
from app.models.project import ProjectState
from app.profile.schema import ProfileSchema
from app.runs.manager import redact
from app.services.cleanup import CleanupError, cleanup_project, cleanup_targets
from app.services.embed import Embedder
from app.services.law_ingest import IngestError, ingest_law
from app.services.screen import Screener
from app.services.update import refresh_and_screen

log = logging.getLogger(__name__)

RESTARTED = "server restarted while the job was active; retry it"
STOPPED = "server stopped while the job was active; retry it"
LAW_UPLOADS = "law_uploads"  # below data_dir; one folder per ingestion job


class LLMUnavailableError(RuntimeError):
    """The work needs OPENAI_API_KEY, which the server does not have."""


class Recovery(BaseModel):
    """What the recover action would start for the company's current state."""

    action: str | None = None  # embed_and_cleanup | rebuild_vectors | cleanup | screen_pending
    reason: str
    needs_llm: bool = False
    busy: str | None = None  # an active job or step blocks recovery right now

    @property
    def available(self) -> bool:
        return self.action is not None


def law_upload_dir(settings: Settings, job_id: str) -> Path:
    return settings.data_dir / LAW_UPLOADS / job_id


class JobManager:
    def __init__(
        self,
        repository: Repository,
        schema: ProfileSchema,
        settings: Settings,
        client: AsyncOpenAI | None,
    ):
        self.repository = repository
        self.schema = schema
        self.settings = settings
        self.client = client
        self._tasks: set[asyncio.Task] = set()
        self._handlers: dict[JobOperation, Callable[[Job], Awaitable[dict[str, Any]]]] = {
            JobOperation.LAW_INGEST: self._ingest,
            JobOperation.SCREEN: self._screen,
            JobOperation.ADVANCE: self._advance,
            JobOperation.REFRESH: self._refresh,
            JobOperation.SCREENING_RUN: self._screening_run,
        }

    async def startup(self) -> None:
        """Mark unfinished jobs interrupted and clear abandoned step markers. No job is
        resumed: the UI retries explicitly."""
        interrupted = await self.repository.interrupt_active_jobs(RESTARTED)
        markers = await self.repository.clear_step_markers()
        if interrupted or markers:
            log.warning(
                "after a restart: %d job(s) interrupted, %d step marker(s) cleared",
                interrupted,
                markers,
            )

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def join(self) -> None:
        """Wait until no job task is left (tests, demo scripts)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # Submission ------------------------------------------------------------------------

    async def submit(
        self,
        operation: JobOperation,
        company_id: str | None = None,
        params: dict[str, Any] | None = None,
        private: dict[str, Any] | None = None,
        *,
        retry_of: str | None = None,
        job_id: str | None = None,
    ) -> Job:
        """Persist the job, then schedule it. BusyError when a conflicting job is active."""
        params = params or {}
        if company_id is not None:
            key = f"company:{company_id}"
        elif operation is JobOperation.LAW_INGEST:
            key = f"law:{params['content_hash']}"
        else:
            key = f"{operation}:all"
        job = await self.repository.create_job(
            Job(
                id=job_id or uuid4().hex,
                operation=operation,
                company_id=company_id,
                params=params,
                private=private or {},
                retry_of=retry_of,
                dedupe_key=key,
            )
        )
        task = asyncio.create_task(self._execute(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        log.info("job %s queued: %s company=%s", job.id, operation, company_id)
        return job

    def require_llm(self) -> None:
        if self.client is None:
            raise LLMUnavailableError("no OPENAI_API_KEY configured on the server")

    async def submit_advance(self, company_id: str, *, reason: str = "confirm") -> Job:
        return await self.submit(JobOperation.ADVANCE, company_id, {"reason": reason})

    async def submit_refresh(self, company_id: str, changed: set[str]) -> Job:
        return await self.submit(JobOperation.REFRESH, company_id, {"changed": sorted(changed)})

    async def retry(self, job_id: str) -> Job:
        """A new attempt of a failed or interrupted job (never an email)."""
        job = await self.repository.get_job(job_id)
        if job is None:
            raise NotFoundError(f"no job with id {job_id}")
        if not job.status.retryable:
            raise StateError(
                f"job {job_id} is {job.status}: only failed or interrupted jobs can be retried"
            )
        later = await self.repository.list_jobs(retry_of=job_id)
        if later:
            raise StateError(f"job {job_id} was already retried as job {later[0].id}")
        stored = Path(job.private.get("path", ""))
        if job.operation is JobOperation.LAW_INGEST and not stored.is_file():
            raise StateError(f"the upload of job {job_id} is no longer stored; upload again")
        if job.operation is not JobOperation.SCREEN or not job.params.get("dry_run"):
            self.require_llm()
        return await self.submit(
            job.operation, job.company_id, job.params, job.private, retry_of=job.id
        )

    # Recovery --------------------------------------------------------------------------

    async def recovery(self, company_id: str) -> Recovery:
        """Select the work from the current state (no job record needed)."""
        state = await self.repository.get_project_state(company_id)
        if state is None:
            return Recovery(reason="nothing published yet")
        busy = await self.repository.company_busy(company_id, self.settings.step_lock_timeout_s)
        if busy:
            return Recovery(reason=f"busy: {busy}", busy=busy)
        runs = await self.repository.list_runs(company_id)
        if (
            runs
            and runs[0].started_by == "update_dd"
            and await self.repository.get_profile_draft(company_id)
        ):
            return Recovery(reason="new documents await advisor review")
        if state.state is ProjectState.PUBLISHED:
            return Recovery(reason="not confirmed yet; use rerun to process the documents again")
        if state.state is ProjectState.CONFIRMED:
            return Recovery(
                action="embed_and_cleanup", reason="confirmed, not embedded", needs_llm=True
            )
        if state.embeddings_stale:
            return Recovery(
                action="rebuild_vectors", reason="vectors stale after an update", needs_llm=True
            )
        if state.state is ProjectState.EMBEDDED:
            return Recovery(action="cleanup", reason="embedded, working data not cleaned up")
        try:
            targets = await cleanup_targets(self.repository, self.settings, company_id)
        except CleanupError as exc:
            return Recovery(reason=redact(str(exc), self.settings))
        if any(path.exists() for path in targets):
            return Recovery(action="cleanup", reason="uploaded files are left after cleanup")
        if self.settings.screen_after_update:
            status = await self.repository.screening_status(company_id)
            if status.pending_changes or status.incomplete_laws or status.stale_decisions:
                return Recovery(
                    action="screen_pending", reason="screening of changes pending", needs_llm=True
                )
        return Recovery(reason="nothing to recover")

    async def recover(self, company_id: str) -> tuple[Recovery, Job | None]:
        plan = await self.recovery(company_id)
        if plan.busy:
            raise BusyError(f"project '{company_id}' is busy ({plan.busy}); wait for it")
        state = await self.repository.get_project_state(company_id)
        if state is None or state.state is ProjectState.PUBLISHED:
            current = state.state if state else "not published"
            raise StateError(
                f"project '{company_id}' is {current}: recovery starts after confirmation; "
                "before it, use rerun"
            )
        if not plan.available:
            return plan, None
        if plan.needs_llm:
            self.require_llm()
        return plan, await self.submit_advance(company_id, reason=f"recover: {plan.action}")

    # Execution -------------------------------------------------------------------------

    async def _execute(self, job: Job) -> None:
        repository = self.repository
        try:
            job.status, job.started_at = JobStatus.RUNNING, datetime.now(UTC)
            await repository.save_job(job)
            job.result = await self._handlers[job.operation](job)
            job.status = JobStatus.SUCCEEDED
        except asyncio.CancelledError:
            job.status, job.error = JobStatus.INTERRUPTED, STOPPED
            job.finished_at = datetime.now(UTC)
            await repository.save_job(job)
            raise
        except Exception as exc:
            log.warning("job %s (%s) failed: %s", job.id, job.operation, type(exc).__name__)
            job.status = JobStatus.FAILED
            job.error = redact(f"{type(exc).__name__}: {exc}", self.settings)[:2000]
        job.finished_at = datetime.now(UTC)
        await repository.save_job(job)

    async def _advance(self, job: Job) -> dict[str, Any]:
        """Bring the project forward from its current state: embed (or fully rebuild stale
        vectors), clean up, then the configured pending screening. Never confirms again and
        never sends an email."""
        cid, result = job.company_id, {}
        state = await self.repository.get_project_state(cid)
        if state is None or state.state is ProjectState.PUBLISHED:
            current = state.state if state else "not published"
            raise StateError(f"project '{cid}' is {current}: nothing to advance before confirm")
        runs = await self.repository.list_runs(cid)
        if (
            runs
            and runs[0].started_by == "update_dd"
            and await self.repository.get_profile_draft(cid)
        ):
            raise StateError("new documents await advisor review before cleanup")
        if state.state is ProjectState.CONFIRMED or state.embeddings_stale:
            if self.client is None:
                await self.repository.record_error(
                    cid, "embed failed: no OPENAI_API_KEY configured on the server"
                )
                self.require_llm()
            report = await Embedder(self.repository, self.schema, self.settings, self.client).run(
                cid
            )
            result["embedded"] = {"written": report.written, "fallback": report.fallback}
        targets = await cleanup_targets(self.repository, self.settings, cid)
        state = await self.repository.get_project_state(cid)
        if state.state is ProjectState.EMBEDDED or any(p.exists() for p in targets):
            try:
                cleaned = await cleanup_project(self.repository, self.settings, cid)
            except Exception as exc:
                await self.repository.record_error(
                    cid, redact(f"cleanup failed: {exc}", self.settings)[:2000]
                )
                raise
            result["cleanup"] = {
                "rows_deleted": cleaned.rows_deleted,
                "files_deleted": len(cleaned.files_deleted),
            }
        if self.settings.screen_after_update:
            status = await self.repository.screening_status(cid)
            if status.pending_changes or status.incomplete_laws or status.stale_decisions:
                self.require_llm()
                report = await Screener(self.repository, self.settings, self.client).screen_pending(
                    cid
                )
                result["screening"] = report.summary()
        return result

    async def _refresh(self, job: Job) -> dict[str, Any]:
        """The shared post-update workflow (services/update.py)."""
        if self.client is None:
            await self.repository.record_error(
                job.company_id, "embedding refresh failed: no OPENAI_API_KEY configured"
            )
            self.require_llm()
        try:
            report = await refresh_and_screen(
                self.repository,
                self.schema,
                self.settings,
                self.client,
                job.company_id,
                set(job.params.get("changed", [])),
            )
        except Exception as exc:
            await self.repository.record_error(
                job.company_id, redact(str(exc), self.settings)[:2000]
            )
            raise
        return {"refreshed": True, "screening": report.summary() if report else None}

    async def _screen(self, job: Job) -> dict[str, Any]:
        dry_run = bool(job.params.get("dry_run"))
        if not dry_run:
            self.require_llm()
        reports = await Screener(self.repository, self.settings, self.client).screen(
            job.company_id,
            job.params.get("law_id"),
            dry_run=dry_run,
            force=bool(job.params.get("force")),
        )
        summaries = [r.summary() for r in reports]
        return {"reports": summaries, "complete": all(r.complete for r in reports)}

    async def _screening_run(self, job: Job) -> dict[str, Any]:
        self.require_llm()
        source = self.settings.law_source_dir
        if not source.is_dir():
            raise StateError("the configured law source folder does not exist")
        new_ids = []
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            digest = sha256(await asyncio.to_thread(path.read_bytes)).hexdigest()
            if await self.repository.find_law(digest):
                continue
            try:
                report = await ingest_law(path, self.repository, self.settings, self.client)
            except DuplicateLawError:
                continue  # another API ingestion stored the same source concurrently
            new_ids.append(str(report.law_id))
        eligible = {
            s.company_id
            for s in await self.repository.list_project_states()
            if s.state in {ProjectState.EMBEDDED, ProjectState.CLEANED}
        }
        reports = await Screener(self.repository, self.settings, self.client).screen()
        unfinished = sum(
            r.company_id in eligible and r.status in {"failed", "refused"} for r in reports
        )
        return {
            "newLawChangeIds": new_ids,
            "lawChanges": len(new_ids),
            "companiesScreened": sum(r.status in {"screened", "nothing_to_do"} for r in reports),
            "affectedCompanyIds": [r.company_id for r in reports if r.hits],
            "hits": sum(r.hits for r in reports),
            "deferred": sum(len(r.deferred) + len(r.unjudged) for r in reports) + unfinished,
            "complete": not unfinished and all(r.complete for r in reports),
        }

    async def _ingest(self, job: Job) -> dict[str, Any]:
        """Ingest the stored upload; its folder is removed only after success, so a failed
        or interrupted attempt can be retried with the same input."""
        self.require_llm()
        path = Path(job.private.get("path", ""))
        if not path.is_file():
            raise IngestError("the uploaded file is no longer stored; upload it again")
        report = await ingest_law(
            path,
            self.repository,
            self.settings,
            self.client,
            replace=bool(job.params.get("replace")),
            filename=job.params.get("filename"),
        )
        root = (self.settings.data_dir / LAW_UPLOADS).resolve()
        folder = path.resolve().parent
        if folder != root and folder.is_relative_to(root):
            await asyncio.to_thread(shutil.rmtree, folder, True)
        result = report.model_dump(mode="json")
        if self.settings.screen_after_ingest:
            # The law is stored either way; a screening problem is reported, not a failed
            # ingestion (a retry would only meet the duplicate). POST /api/screenings repeats it.
            try:
                reports = await Screener(self.repository, self.settings, self.client).screen(
                    law_id=report.law_id
                )
            except Exception as exc:
                result["screening_error"] = redact(f"{type(exc).__name__}: {exc}", self.settings)
            else:
                result["screening"] = [
                    {k: v for k, v in r.summary().items() if k != "scored"} for r in reports
                ]
        return result


def job_view(job: Job | None) -> JobView | None:
    return job.view() if job else None
