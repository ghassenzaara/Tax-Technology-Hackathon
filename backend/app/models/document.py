"""Normalized document format: the contract between parsing (step 2) and the agents (step 3).

Every page keeps its own number so each extracted value can cite `source_page`.
For Excel, one sheet is one page and `page_no` is the sheet's position in the workbook.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class DocType(StrEnum):
    TEXT_PDF = "text_pdf"
    SCANNED_PDF = "scanned_pdf"
    MIXED_PDF = "mixed_pdf"
    PDF = "pdf"  # PDF whose pages could not be read, so text vs scan is unknown
    EXCEL = "excel"
    UNSUPPORTED = "unsupported"


class DocStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"  # usable, but at least one page has a warning
    FAILED = "failed"  # nothing usable
    UNSUPPORTED = "unsupported"  # skipped on purpose, listed for the advisor


class PageKind(StrEnum):
    TEXT = "text"  # pdfplumber
    OCR = "ocr"  # GPT vision transcription
    SHEET = "sheet"  # Excel worksheet


class Table(BaseModel):
    rows: list[list[str]]
    has_header: bool = True


class Page(BaseModel):
    page_no: int = Field(ge=1)
    kind: PageKind
    text: str = ""
    tables: list[Table] = []
    sheet_name: str | None = None
    warnings: list[str] = []
    needs_review: bool = False  # true for every OCR page

    @property
    def label(self) -> str:
        if self.kind is PageKind.SHEET:
            return f"Sheet {self.page_no}: {self.sheet_name}"
        if self.kind is PageKind.OCR:
            return f"Page {self.page_no} (OCR, needs review)"
        return f"Page {self.page_no}"


class Timings(BaseModel):
    parse_s: float = 0.0
    ocr_s: float = 0.0
    total_s: float = 0.0


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class Document(BaseModel):
    doc_id: str
    filename: str
    relative_path: str
    type: DocType
    status: DocStatus
    pages: list[Page] = []
    warnings: list[str] = []
    timings: Timings = Field(default_factory=Timings)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)

    def to_markdown(self) -> str:
        """Single text blob for the extraction agent: one marked section per page.

        Page text comes first, then its tables. Sheet pages already hold their table
        as markdown text, so their tables are not rendered twice.
        """
        parts = [f"# Document: {self.filename}"]
        for page in self.pages:
            parts.append(f"## {page.label}")
            if page.text:
                parts.append(page.text)
            if page.kind is not PageKind.SHEET:
                parts.extend(table_to_markdown(table) for table in page.tables)
        return "\n\n".join(parts) + "\n"


def table_to_markdown(table: Table) -> str:
    if not table.rows:
        return ""
    width = max(len(row) for row in table.rows)
    rows = [[_md_cell(cell) for cell in row] + [""] * (width - len(row)) for row in table.rows]
    header, body = (rows[0], rows[1:]) if table.has_header else ([""] * width, rows)
    lines = [_md_row(header), _md_row(["---"] * width), *(_md_row(row) for row in body)]
    return "\n".join(lines)


def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _md_cell(cell: str) -> str:
    return cell.replace("|", "\\|").replace("\n", " ")
