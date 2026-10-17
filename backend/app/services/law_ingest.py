"""Steps 11 and 12 service: ingest one law PDF (parse, metadata, split, embed, store).

The same file twice is refused before any LLM call (content hash); `replace` deletes the
stored law with its sections and matches first. Law, sections and vectors are written in
one transaction after every vector came back, so a failed ingest writes nothing.
"""

import asyncio
import hashlib
import logging
from pathlib import Path
from time import perf_counter

from openai import AsyncOpenAI

from app.config import Settings
from app.db.repository import DuplicateLawError, Repository
from app.ingest.pipeline import DocumentParser
from app.laws.metadata import read_metadata
from app.laws.split import split_law
from app.llm import embed_texts
from app.models.document import DocStatus
from app.models.law import IngestReport, Law

log = logging.getLogger(__name__)


class IngestError(RuntimeError):
    """The law could not be ingested; nothing was written."""


def content_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def ingest_law(
    path: Path,
    repository: Repository,
    settings: Settings,
    client: AsyncOpenAI,
    *,
    replace: bool = False,
) -> IngestReport:
    """Parse, describe, split and embed one law; raises DuplicateLawError or IngestError."""
    start = perf_counter()
    path = Path(path)
    if not path.is_file():
        raise IngestError(f"not a file: {path}")
    digest = content_hash(path)
    existing = await repository.find_law(digest)
    if existing is not None and not replace:
        raise DuplicateLawError(
            f"law '{existing.title}' (id {existing.id}) is already stored; use --replace"
        )

    doc = await DocumentParser(settings, client).parse_file(path)
    if doc.status in (DocStatus.FAILED, DocStatus.UNSUPPORTED):
        raise IngestError(f"cannot parse {path.name}: {'; '.join(doc.warnings)}")
    slots = asyncio.Semaphore(settings.embed_concurrency)
    try:
        metadata, tokens = await read_metadata(doc, settings, client, slots)
        vocabulary = await repository.list_norm_codes()
        split = split_law(doc, settings, vocabulary, list(metadata.tax_types))
        if not split.sections:
            raise IngestError(f"{path.name} has no law text after removing boilerplate")
        vectors = await embed_texts(
            client, settings, slots, [s.embedding_text() for s in split.sections]
        )
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError(f"ingest of {path.name} failed: {exc}") from exc
    for section, vector in zip(split.sections, vectors, strict=True):
        section.model, section.vector = settings.embedding_model, vector

    law = Law(**metadata.model_dump(), filename=path.name, content_hash=digest)
    law_id = await repository.save_law(law, split.sections, replace=replace)
    tokens += doc.token_usage  # OCR of scanned pages
    report = IngestReport(
        law_id=law_id,
        title=law.title,
        kind=law.kind,
        status=law.status,
        published_at=law.published_at,
        effective_at=law.effective_at,
        summary=law.summary,
        change_summary=law.change_summary,
        strategy=split.strategy,
        sections=len(split.sections),
        norms_found=split.norms_found,
        norms_unmatched=split.norms_unmatched,
        dropped=split.dropped,
        replaced=existing is not None,
        input_tokens=tokens.input_tokens,
        output_tokens=tokens.output_tokens,
        seconds=perf_counter() - start,
    )
    log.info("%s; law=%d strategy=%s", report.headline(), law_id, split.strategy)
    return report
