"""Assemble parser output into a `Document`: stable id, final type, status, cleaned text."""

import hashlib
import re
from collections.abc import Iterable
from pathlib import Path

from app.ingest.discover import DiscoveredFile, FileFormat
from app.models.document import (
    DocStatus,
    DocType,
    Document,
    Page,
    PageKind,
    Timings,
    TokenUsage,
)

_BLANK_RUNS = re.compile(r"\n{3,}")


def compute_doc_id(path: Path, relative_path: str) -> str:
    """Short hash of path + content: deterministic across reruns, distinct for same-name files."""
    digest = hashlib.sha256(relative_path.encode("utf-8"))
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        pass  # unreadable file: id from the path alone, the parser reports the error
    return digest.hexdigest()[:16]


def clean_text(text: str) -> str:
    """Whitespace cleanup only: strip trailing spaces, collapse runs of blank lines."""
    lines = "\n".join(line.rstrip() for line in text.splitlines())
    return _BLANK_RUNS.sub("\n\n", lines).strip()


def build_document(
    file: DiscoveredFile,
    doc_id: str,
    *,
    pages: Iterable[Page] = (),
    warnings: Iterable[str] = (),
    error: str | None = None,
    timings: Timings | None = None,
    token_usage: TokenUsage | None = None,
) -> Document:
    pages = sorted(
        (page.model_copy(update={"text": clean_text(page.text)}) for page in pages),
        key=lambda page: page.page_no,
    )
    notes = ([error] if error else []) + list(warnings)
    usable = any(page.text or page.tables for page in pages)

    if file.file_format is FileFormat.UNSUPPORTED:
        status = DocStatus.UNSUPPORTED
    elif error or not usable:
        status = DocStatus.FAILED
        if not error:
            notes.append("no usable content extracted")
    elif any(page.warnings for page in pages):
        status = DocStatus.PARTIAL
    else:
        status = DocStatus.OK

    return Document(
        doc_id=doc_id,
        filename=file.path.name,
        relative_path=file.relative_path,
        type=_derive_type(file.file_format, pages),
        status=status,
        pages=pages,
        warnings=notes,
        timings=timings or Timings(),
        token_usage=token_usage or TokenUsage(),
    )


def _derive_type(file_format: FileFormat, pages: list[Page]) -> DocType:
    if file_format is FileFormat.EXCEL:
        return DocType.EXCEL
    if file_format is FileFormat.UNSUPPORTED:
        return DocType.UNSUPPORTED
    kinds = {page.kind for page in pages}
    if kinds == {PageKind.TEXT}:
        return DocType.TEXT_PDF
    if kinds == {PageKind.OCR}:
        return DocType.SCANNED_PDF
    if kinds == {PageKind.TEXT, PageKind.OCR}:
        return DocType.MIXED_PDF
    return DocType.PDF
