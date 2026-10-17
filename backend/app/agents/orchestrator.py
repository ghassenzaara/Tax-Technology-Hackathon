"""Consumer side of the brief's producer/consumer design (steps 5 to 7).

An agent task starts the moment its document arrives from the stream, without waiting for
the other documents. Tasks share one semaphore, held only while an LLM request is in
flight. Each document's candidates are written as soon as it finishes. The merge is the
only step that waits for everything.
"""

import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from time import perf_counter

from openai import AsyncOpenAI
from tenacity import AsyncRetrying, stop_after_attempt, wait_random_exponential

from app.agents.extractor import Extractor
from app.config import Settings, get_settings
from app.db.repository import Repository
from app.llm import make_client
from app.models.document import Document
from app.models.extraction import (
    DocumentExtraction,
    ExtractionStatus,
    ExtractionSummary,
    summarize_extractions,
)
from app.models.profile import ProfileDraft
from app.profile.merge import merge_candidates
from app.profile.schema import ProfileSchema

log = logging.getLogger(__name__)


@dataclass
class _StreamEnd:
    count: int


@dataclass
class _StreamError:
    error: BaseException


@dataclass
class ExtractionRun:
    extractions: list[DocumentExtraction]
    draft: ProfileDraft
    summary: ExtractionSummary


class Orchestrator:
    def __init__(
        self,
        schema: ProfileSchema,
        repository: Repository,
        settings: Settings | None = None,
        client: AsyncOpenAI | None = None,
    ):
        self.schema = schema
        self.repository = repository
        self.settings = settings or get_settings()
        self.client = client if client is not None else make_client(self.settings)
        self.extractor = Extractor(
            schema,
            settings=self.settings,
            client=self.client,
            slots=asyncio.Semaphore(self.settings.agent_concurrency),
        )

    async def extract_stream(
        self, documents: AsyncIterable[Document], company_id: str
    ) -> AsyncIterator[DocumentExtraction]:
        """Yield one result per document received, in completion order.

        Errors of the document stream itself (for example a missing folder) propagate.
        """
        queue: asyncio.Queue = asyncio.Queue()
        tasks: list[asyncio.Task] = []

        async def process(doc: Document) -> None:
            try:
                result = await self._process(doc, company_id)
            except Exception as exc:  # last line of defense: always one result per document
                log.exception("unexpected error processing %s", doc.relative_path)
                result = DocumentExtraction(
                    company_id=company_id,
                    doc_id=doc.doc_id,
                    filename=doc.relative_path,
                    status=ExtractionStatus.FAILED,
                    warnings=[f"unexpected error: {type(exc).__name__}: {exc}"],
                )
            await queue.put(result)

        async def feed() -> None:
            try:
                async for doc in documents:
                    tasks.append(asyncio.create_task(process(doc)))
            except Exception as exc:
                await queue.put(_StreamError(exc))
            else:
                await queue.put(_StreamEnd(len(tasks)))

        feeder = asyncio.create_task(feed())
        expected, received = None, 0
        try:
            while expected is None or received < expected:
                item = await queue.get()
                if isinstance(item, _StreamEnd):
                    expected = item.count
                elif isinstance(item, _StreamError):
                    raise item.error
                else:
                    received += 1
                    yield item
        finally:  # consumer stopped early or the stream failed: do not leave work running
            feeder.cancel()
            for task in tasks:
                task.cancel()

    async def run(self, documents: AsyncIterable[Document], company_id: str) -> ExtractionRun:
        """Extract everything, then merge into a draft and save it."""
        start = perf_counter()
        extractions = [e async for e in self.extract_stream(documents, company_id)]
        draft = await self.merge(company_id, extractions, run_scoped=True)
        summary = summarize_extractions(extractions, perf_counter() - start)
        log.info(
            "%s; tokens in=%d out=%d; %s",
            summary.headline(),
            summary.token_usage.input_tokens,
            summary.token_usage.output_tokens,
            ", ".join(f"{s}={n}" for s, n in sorted(summary.by_status.items())),
        )
        return ExtractionRun(extractions=extractions, draft=draft, summary=summary)

    async def merge(
        self,
        company_id: str,
        extractions: Iterable[DocumentExtraction] = (),
        *,
        run_scoped: bool = False,
    ) -> ProfileDraft:
        """Merge stored candidates plus the in-memory ones of documents the DB missed.

        `run_scoped` (after a run): only documents of this run count, so a file removed
        from the folder since an earlier run no longer contributes. Without it (merge
        only) every stored document of the company counts. A write-failed document
        always uses its fresh in-memory candidates, never its stale stored rows.
        Extraction records supply the source class (rank) of the candidates.
        """
        extractions = list(extractions)
        records = await self.known_extractions(company_id, extractions, run_scoped=run_scoped)
        in_run = {e.doc_id for e in records}
        write_failed = {e.doc_id for e in extractions if e.status is ExtractionStatus.WRITE_FAILED}
        candidates = [
            c
            for c in await self.repository.list_candidates(company_id)
            if c.source_doc not in write_failed and (not run_scoped or c.source_doc in in_run)
        ]
        for e in extractions:
            if e.status is ExtractionStatus.WRITE_FAILED:
                candidates += e.candidates
        draft = merge_candidates(
            candidates,
            self.schema,
            company_id=company_id,
            max_granularity=self.settings.rounding_max_granularity,
            extractions=records,
        )
        # Review and confirmation read the stored draft: a draft that cannot be saved
        # (after the configured retries) fails the run instead of being published.
        await self._retrying(self.repository.save_profile_draft, draft)
        return draft

    async def known_extractions(
        self,
        company_id: str,
        current: Iterable[DocumentExtraction] = (),
        *,
        run_scoped: bool = False,
    ) -> list[DocumentExtraction]:
        """Stored extraction records, overridden by the ones of the current run. A current
        attempt without document metadata (failed, skipped) keeps the stored metadata,
        matching the candidates the repository kept for it."""
        stored = {e.doc_id: e for e in await self.repository.list_extractions(company_id)}
        records = {} if run_scoped else dict(stored)
        for e in current:
            previous = stored.get(e.doc_id)
            if previous is not None and e.document_class is None:
                e = e.model_copy(
                    update={
                        "document_class": previous.document_class,
                        "document_title": previous.document_title,
                        "document_date": previous.document_date,
                        "warnings": [*e.warnings, "earlier candidates of this document kept"],
                    }
                )
            records[e.doc_id] = e
        return sorted(records.values(), key=lambda e: (e.filename, e.doc_id))

    async def publish(
        self,
        draft: ProfileDraft,
        extractions: Iterable[DocumentExtraction],
        *,
        run_scoped: bool = False,
    ):
        """Write the draft into the profile schema as unconfirmed rows (see profil_writer)."""
        records = await self.known_extractions(draft.company_id, extractions, run_scoped=run_scoped)
        report = await self.repository.publish_profile(draft, records, self.schema, self.settings)
        log.info(
            "published project=%s rows=%s hints=%d skipped=%d",
            report.project,
            report.rows,
            report.hints,
            len(report.skipped),
        )
        return report

    async def _process(self, doc: Document, company_id: str) -> DocumentExtraction:
        """Isolation boundary: extraction never raises; a failed write is flagged, not fatal."""
        extraction = await self.extractor.extract(doc, company_id)
        try:
            await self._write(extraction)
        except Exception as exc:
            log.exception("candidate write failed for %s", doc.relative_path)
            extraction.status = ExtractionStatus.WRITE_FAILED
            extraction.warnings.append(f"DB write failed: {type(exc).__name__}: {exc}")
        _log_extraction(extraction)
        return extraction

    async def _write(self, extraction: DocumentExtraction) -> None:
        await self._retrying(self.repository.replace_document_candidates, extraction)

    async def _retrying(self, write, *args) -> None:
        """A database write with the configured attempts and backoff; the last error raises."""
        retrying = AsyncRetrying(
            wait=wait_random_exponential(
                min=self.settings.agent_backoff_min_s, max=self.settings.agent_backoff_max_s
            ),
            stop=stop_after_attempt(self.settings.db_write_attempts),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                await write(*args)


def _log_extraction(e: DocumentExtraction) -> None:
    log.info(
        "extracted doc_id=%s file=%s status=%s chunks=%d repair=%s fields=%d candidates=%d "
        "agent=%.2fs tokens_in=%d tokens_out=%d warnings=%d",
        e.doc_id,
        e.filename,
        e.status,
        e.chunks,
        e.repair_used,
        e.fields_found,
        len(e.candidates),
        e.agent_s,
        e.token_usage.input_tokens,
        e.token_usage.output_tokens,
        len(e.warnings),
    )
