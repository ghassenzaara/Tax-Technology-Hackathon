"""Producer side of the brief's producer/consumer design.

`DocumentParser.parse_folder` yields each `Document` as soon as it is finished, so the
future agent orchestrator can start extracting without waiting for the slowest file.
Blocking parsers run in threads under `parse_concurrency`; OCR calls run under a separate
`ocr_concurrency` limit, so slow scans never hold up fast text files.
"""

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from time import perf_counter

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.ingest.discover import DiscoveredFile, FileFormat, classify, discover
from app.ingest.excel import parse_excel
from app.ingest.normalize import build_document, compute_doc_id
from app.ingest.pdf_ocr import transcribe_pages
from app.ingest.pdf_text import parse_pdf_text
from app.llm import make_client
from app.models.document import DocStatus, Document, Timings, TokenUsage

log = logging.getLogger(__name__)


class RunSummary(BaseModel):
    documents: int
    by_status: dict[DocStatus, int]
    wall_s: float
    token_usage: TokenUsage
    per_document: dict[str, Timings]  # keyed by relative path

    def headline(self) -> str:
        return f"{self.documents} documents in {self.wall_s:.1f} seconds"


class DocumentParser:
    """Parses files into normalized documents. Knows nothing about jobs or uploads."""

    def __init__(self, settings: Settings | None = None, client: AsyncOpenAI | None = None):
        self.settings = settings or get_settings()
        self.client = client if client is not None else make_client(self.settings)
        self._parse_slots = asyncio.Semaphore(self.settings.parse_concurrency)
        self._ocr_slots = asyncio.Semaphore(self.settings.ocr_concurrency)

    async def parse_folder(self, folder: Path) -> AsyncIterator[Document]:
        """Yield one Document per discovered file, in completion order."""
        root = Path(folder)
        if not root.is_dir():
            raise NotADirectoryError(f"not a folder: {root}")
        tasks = [
            asyncio.create_task(self._parse_isolated(f)) for f in discover(root, self.settings)
        ]
        try:
            for next_done in asyncio.as_completed(tasks):
                yield await next_done
        finally:
            for task in tasks:  # consumer stopped early: do not leave work running
                task.cancel()

    async def parse_file(self, path: Path, root: Path | None = None) -> Document:
        path = Path(path)
        return await self._parse_isolated(classify(path, root or path.parent, self.settings))

    async def _parse_isolated(self, file: DiscoveredFile) -> Document:
        """Isolation boundary: any exception becomes a failed Document, never a crashed run."""
        start = perf_counter()
        doc_id = await asyncio.to_thread(compute_doc_id, file.path, file.relative_path)
        try:
            doc = await self._parse(file, doc_id)
        except Exception as exc:
            log.exception("unexpected error parsing %s", file.relative_path)
            doc = build_document(
                file, doc_id, error=f"unexpected error: {type(exc).__name__}: {exc}"
            )
        doc.timings.total_s = perf_counter() - start
        _log_document(doc)
        return doc

    async def _parse(self, file: DiscoveredFile, doc_id: str) -> Document:
        if file.problem:
            return build_document(file, doc_id, error=file.problem)
        if file.file_format is FileFormat.EXCEL:
            return await self._parse_excel(file, doc_id)
        return await self._parse_pdf(file, doc_id)

    async def _parse_excel(self, file: DiscoveredFile, doc_id: str) -> Document:
        timings = Timings()
        async with self._parse_slots:
            start = perf_counter()
            result = await asyncio.to_thread(parse_excel, file.path, self.settings.excel_row_cap)
            timings.parse_s = perf_counter() - start
        return build_document(
            file,
            doc_id,
            pages=result.pages,
            warnings=result.warnings,
            error=result.error,
            timings=timings,
        )

    async def _parse_pdf(self, file: DiscoveredFile, doc_id: str) -> Document:
        timings = Timings()
        async with self._parse_slots:
            start = perf_counter()
            result = await asyncio.to_thread(
                parse_pdf_text, file.path, self.settings.scan_min_chars
            )
            timings.parse_s = perf_counter() - start

        pages = {page.page_no: page for page in result.pages}
        usage = TokenUsage()
        if result.needs_ocr:  # parse slot already released: OCR waits only on OCR slots
            start = perf_counter()
            ocr_pages, usage = await transcribe_pages(
                file.path,
                result.needs_ocr,
                client=self.client,
                settings=self.settings,
                slots=self._ocr_slots,
            )
            timings.ocr_s = perf_counter() - start
            for page in ocr_pages:  # OCR page replaces the text page, keeping its warnings
                page.warnings = pages[page.page_no].warnings + page.warnings
                pages[page.page_no] = page

        return build_document(
            file,
            doc_id,
            pages=pages.values(),
            warnings=result.warnings,
            error=result.error,
            timings=timings,
            token_usage=usage,
        )


def summarize(documents: Iterable[Document], wall_s: float) -> RunSummary:
    documents = list(documents)
    return RunSummary(
        documents=len(documents),
        by_status=dict(Counter(doc.status for doc in documents)),
        wall_s=wall_s,
        token_usage=sum((doc.token_usage for doc in documents), TokenUsage()),
        per_document={doc.relative_path: doc.timings for doc in documents},
    )


def _log_document(doc: Document) -> None:
    warning_count = len(doc.warnings) + sum(len(page.warnings) for page in doc.pages)
    log.info(
        "parsed doc_id=%s file=%s type=%s status=%s pages=%d parse=%.2fs ocr=%.2fs "
        "total=%.2fs tokens_in=%d tokens_out=%d warnings=%d",
        doc.doc_id,
        doc.relative_path,
        doc.type,
        doc.status,
        len(doc.pages),
        doc.timings.parse_s,
        doc.timings.ocr_s,
        doc.timings.total_s,
        doc.token_usage.input_tokens,
        doc.token_usage.output_tokens,
        warning_count,
    )
