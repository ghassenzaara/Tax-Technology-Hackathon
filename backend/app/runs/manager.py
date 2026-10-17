"""Run manager (step 0): background execution of steps 1 to 7 plus publish for an upload.

A run lists the upload folder, creates one `pending` progress row per discovered file before
any work starts, then drives the existing parser and orchestrator (agents start as documents
arrive) and updates the file's row on every result. At the end it merges and publishes the
draft into `profil` (state published), but only when at least one document is usable;
otherwise the run finishes with the project state unchanged. One failing document never
fails the run; a crash of the run itself sets state failed with the error text.

Concurrency: at most one active run per company (a second start is refused) and a global cap
on simultaneous runs (later runs wait as `queued`). At server start, runs left queued or
running are marked interrupted; a rerun resumes cheaply because candidate writes are
idempotent. After a confirm the manager runs embed then cleanup (steps 9 and 10) in the
background, and after an update the embedding refresh.
"""

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from openai import AsyncOpenAI

from app.agents.orchestrator import Orchestrator
from app.config import Settings
from app.db.repository import NotFoundError, Repository, RunActiveError, StateError
from app.ingest.discover import discover
from app.ingest.pipeline import DocumentParser
from app.models.document import Document
from app.models.extraction import DocumentExtraction, ExtractionStatus, summarize_extractions
from app.models.project import ProjectState
from app.models.run import Run, RunDocument, RunState
from app.profile.schema import ProfileSchema
from app.services.cleanup import cleanup_project
from app.services.embed import Embedder

log = logging.getLogger(__name__)

USABLE = (ExtractionStatus.OK, ExtractionStatus.PARTIAL, ExtractionStatus.WRITE_FAILED)
RESTARTED = "server restarted while the run was active; start a rerun"
STOPPED = "server stopped while the run was active; start a rerun"


def redact(text: str, settings: Settings) -> str:
    """Error text for the UI: the server's data folder never leaves the server."""
    for root in sorted({str(settings.data_dir.resolve()), str(settings.data_dir)}, key=len)[::-1]:
        text = text.replace(root, "<data>")
    return text


class RunManager:
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
        self._slots = asyncio.Semaphore(settings.max_active_runs)  # global run cap
        self._lock = asyncio.Lock()  # one start decision at a time
        self._runs: dict[str, asyncio.Task] = {}  # company id -> active run task
        self._tasks: set[asyncio.Task] = set()  # every background task (runs, embed, cleanup)

    async def startup(self) -> None:
        interrupted = await self.repository.interrupt_active_runs(RESTARTED)
        if interrupted:
            log.warning("marked %d run(s) interrupted after a restart", interrupted)

    async def shutdown(self) -> None:
        """Cancel running work; a cancelled run marks itself interrupted."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def join(self) -> None:
        """Wait until no background work is left (tests, demo scripts)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def is_active(self, company_id: str) -> bool:
        task = self._runs.get(company_id)
        return task is not None and not task.done()

    async def start(
        self,
        company_id: str,
        folder: Path,
        *,
        project_name: str | None = None,
        started_by: str = "upload",
    ) -> Run:
        """Create the run and its pending progress rows, then process in the background."""
        async with self._lock:
            latest = await self.repository.list_runs(company_id)
            if self.is_active(company_id) or (latest and latest[0].state.active):
                raise RunActiveError(f"a run of '{company_id}' is already active; wait for it")
            run = Run(
                run_id=uuid4().hex,
                company_id=company_id,
                project_name=project_name,
                folder=str(folder),
                started_by=started_by,
            )
            documents = [
                RunDocument(run_id=run.run_id, company_id=company_id, relative_path=f.relative_path)
                for f in await asyncio.to_thread(discover, folder, self.settings)
            ]
            await self.repository.save_run(run)
            await self.repository.save_run_documents(documents)
            self._runs[company_id] = self._spawn(self._execute(run))
        log.info("run %s queued for %s with %d file(s)", run.run_id, company_id, len(documents))
        return run

    async def rerun(self, company_id: str) -> Run:
        """Process the stored folder again; only before the profile is confirmed."""
        runs = await self.repository.list_runs(company_id)
        state = await self.repository.get_project_state(company_id)
        if not runs:
            if state is None:
                raise NotFoundError(f"unknown company '{company_id}'")
            raise StateError(f"project '{company_id}' has no uploaded folder to process again")
        if state is not None and state.state is not ProjectState.PUBLISHED:
            raise StateError(
                f"project '{company_id}' is {state.state}: a rerun is allowed only before "
                "the profile is confirmed"
            )
        folder = next((Path(r.folder) for r in runs if r.folder), None)
        if folder is None or not folder.is_dir():
            raise StateError(f"the uploaded folder of '{company_id}' no longer exists")
        return await self.start(
            company_id, folder, project_name=runs[0].project_name, started_by="rerun"
        )

    def start_advance(self, company_id: str) -> None:
        """After a confirm: embed (step 9), then clean up (step 10), in the background."""
        self._spawn(self._advance(company_id))

    def start_refresh(self, company_id: str, changed: set[str]) -> None:
        """After an update: refresh the affected vectors in the background (plan 4b.5)."""
        self._spawn(self._refresh(company_id, changed))

    def _spawn(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _execute(self, run: Run) -> None:
        repository = self.repository
        try:
            async with self._slots:
                run.state, run.started_at = RunState.RUNNING, datetime.now(UTC)
                await repository.save_run(run)
                await self._process(run)
            run.state = RunState.FINISHED
        except asyncio.CancelledError:
            run.state, run.last_error = RunState.INTERRUPTED, STOPPED
            run.finished_at = datetime.now(UTC)
            await repository.save_run(run)
            raise
        except Exception as exc:
            log.exception("run %s of %s failed", run.run_id, run.company_id)
            run.state = RunState.FAILED
            run.last_error = redact(f"{type(exc).__name__}: {exc}", self.settings)[:2000]
        run.finished_at = datetime.now(UTC)
        await repository.save_run(run)

    async def _process(self, run: Run) -> None:
        rows = {d.relative_path: d for d in await self.repository.list_run_documents(run.run_id)}
        by_doc: dict[str, RunDocument] = {}
        parser = DocumentParser(self.settings, self.client)
        orchestrator = Orchestrator(self.schema, self.repository, self.settings, self.client)

        async def parsed():
            async for doc in parser.parse_folder(Path(run.folder)):
                row = rows.setdefault(
                    doc.relative_path,
                    RunDocument(
                        run_id=run.run_id,
                        company_id=run.company_id,
                        relative_path=doc.relative_path,
                    ),
                )
                _record_parse(row, doc)
                by_doc[doc.doc_id] = row
                await self.repository.save_run_documents([row])
                yield doc

        start = perf_counter()
        extractions: list[DocumentExtraction] = []
        async for extraction in orchestrator.extract_stream(parsed(), run.company_id):
            extractions.append(extraction)
            row = by_doc.get(extraction.doc_id)
            if row is not None:
                _record_extraction(row, extraction)
                await self.repository.save_run_documents([row])
        summary = summarize_extractions(extractions, perf_counter() - start)
        log.info(
            "%s; run=%s project=%s tokens in=%d out=%d; %s",
            summary.headline(),
            run.run_id,
            run.company_id,
            summary.token_usage.input_tokens,
            summary.token_usage.output_tokens,
            ", ".join(f"{s}={n}" for s, n in sorted(summary.by_status.items())),
        )
        run.counts = dict(Counter(row.extraction_status for row in rows.values()))
        if not any(e.status in USABLE for e in extractions):
            log.warning("run %s: no usable document, nothing published", run.run_id)
            return
        draft = await orchestrator.merge(run.company_id, extractions, run_scoped=True)
        await orchestrator.publish(draft, extractions, run_scoped=True)
        run.published = True

    async def _advance(self, company_id: str) -> None:
        if self.client is None:
            await self.repository.record_error(
                company_id, "embed failed: no OPENAI_API_KEY configured on the server"
            )
            return
        try:
            await Embedder(self.repository, self.schema, self.settings, self.client).run(company_id)
        except Exception as exc:  # the embedder recorded the error; the state is unchanged
            log.warning("embedding of %s failed: %s", company_id, type(exc).__name__)
            return
        folders = {r.folder for r in await self.repository.list_runs(company_id) if r.folder}
        try:
            await cleanup_project(
                self.repository, self.settings, company_id, [Path(f) for f in sorted(folders)]
            )
        except Exception as exc:
            log.warning("cleanup of %s failed: %s", company_id, type(exc).__name__)
            await self.repository.record_error(
                company_id, redact(f"cleanup failed: {exc}", self.settings)[:2000]
            )

    async def _refresh(self, company_id: str, changed: set[str]) -> None:
        if self.client is None:
            await self.repository.record_error(
                company_id, "embedding refresh failed: no OPENAI_API_KEY configured on the server"
            )
            return
        try:
            await Embedder(self.repository, self.schema, self.settings, self.client).refresh(
                company_id, changed
            )
        except Exception as exc:  # the change stays committed; the stale flag stays set
            log.warning("embedding refresh of %s failed: %s", company_id, type(exc).__name__)


def _record_parse(row: RunDocument, doc: Document) -> None:
    row.doc_id = doc.doc_id
    row.parse_status = str(doc.status)
    row.pages = len(doc.pages)
    row.seconds = round(doc.timings.total_s, 2)
    row.warnings = list(doc.warnings) + [
        f"{page.label}: {w}" for page in doc.pages for w in page.warnings
    ]
    row.input_tokens += doc.token_usage.input_tokens
    row.output_tokens += doc.token_usage.output_tokens


def _record_extraction(row: RunDocument, extraction: DocumentExtraction) -> None:
    row.extraction_status = str(extraction.status)
    row.warnings += [w for w in extraction.warnings if w not in row.warnings]
    row.seconds = round((row.seconds or 0) + extraction.agent_s, 2)
    row.input_tokens += extraction.token_usage.input_tokens
    row.output_tokens += extraction.token_usage.output_tokens
