"""Text PDF parsing with pdfplumber, page by page.

Pages with too little text are flagged for OCR instead of being dropped.
Blocking: call from a worker thread. Never raises; problems become warnings or `error`.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from app.models.document import Page, PageKind, Table


@dataclass
class PdfTextResult:
    pages: list[Page] = field(default_factory=list)  # one per PDF page, in order
    needs_ocr: list[int] = field(default_factory=list)  # page numbers to send to OCR
    warnings: list[str] = field(default_factory=list)
    error: str | None = None  # set only when no page could be read at all


def parse_pdf_text(path: Path, min_chars: int) -> PdfTextResult:
    result = PdfTextResult()
    try:
        with pdfplumber.open(path) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                parsed, needs_ocr = _extract_page(page, page_no, min_chars)
                result.pages.append(parsed)
                if needs_ocr:
                    result.needs_ocr.append(page_no)
    except Exception as exc:  # corrupt structure, encryption, ...
        message = _describe_open_error(exc)
        if result.pages:
            result.warnings.append(f"stopped after page {len(result.pages)}: {message}")
        else:
            result.error = message
    return result


def _extract_page(page, page_no: int, min_chars: int) -> tuple[Page, bool]:
    try:
        text = page.extract_text() or ""
        # TODO: table text also appears in `text`; check on real files whether to strip it.
        tables = [_to_table(raw) for raw in page.extract_tables()]
    except Exception as exc:
        warning = f"text extraction failed ({exc}); sent to OCR"
        return Page(page_no=page_no, kind=PageKind.TEXT, warnings=[warning]), True
    finally:
        page.close()  # frees pdfplumber's per-page cache

    tables = [table for table in tables if table.rows]
    char_count = len("".join(text.split()))
    page_out = Page(page_no=page_no, kind=PageKind.TEXT, text=text, tables=tables)
    return page_out, char_count < min_chars


def _to_table(raw: list[list[str | None]]) -> Table:
    rows = [[" ".join((cell or "").split()) for cell in row] for row in raw]
    return Table(rows=[row for row in rows if any(row)])


def _describe_open_error(exc: Exception) -> str:
    if "password" in repr(exc).lower():
        return "encrypted PDF (password required)"
    return f"cannot read PDF: {type(exc).__name__}: {exc}"
