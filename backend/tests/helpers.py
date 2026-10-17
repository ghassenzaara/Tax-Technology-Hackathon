"""Test file builders and a fake OpenAI client. Fixtures are generated at run time;
real fixtures come from a teammate later. No network is ever used."""

import asyncio
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx2  # openai 3.x HTTP layer; its error classes expect httpx2 responses
import openpyxl
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.models.document import DocStatus, DocType, Document, Page, PageKind

SENTENCE = "Die Muster GmbH betreibt ein Restaurant in Berlin mit 25 Mitarbeitern."


def make_pdf(path: Path, pages: list[str | None]) -> Path:
    """One PDF page per entry: a string becomes a text page, None an image-only (scanned) page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path))
    for content in pages:
        if content is None:
            image = Image.new("RGB", (300, 300), "white")
            ImageDraw.Draw(image).text((20, 20), "Scan", fill="black")
            pdf.drawImage(ImageReader(image), 50, 400, 300, 300)
        else:
            y = 800
            for line in content.splitlines():
                pdf.drawString(50, y, line)
                y -= 15
        pdf.showPage()
    pdf.save()
    return path


def make_simple_excel(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Daten"
    sheet.append(["Kennzahl", "Wert"])
    sheet.append(["Mitarbeiter", 25])
    workbook.save(path)
    return path


def make_rich_excel(path: Path) -> Path:
    """Sheets: data with offset and date, empty, merged header, hidden, uncached formula."""
    workbook = openpyxl.Workbook()
    data = workbook.active
    data.title = "Umsatz"
    data["B2"], data["C2"], data["D2"] = "Jahr", "Umsatz EUR", "Stichtag"
    data["B3"], data["C3"], data["D3"] = 2023, 1234.5, date(2023, 12, 31)

    workbook.create_sheet("Leer")

    merged = workbook.create_sheet("Merged")
    merged["A1"] = "Gesellschafter"
    merged.merge_cells("A1:B1")
    merged.append(["Name", "Anteil"])
    merged.append(["Muster GmbH", 0.5])

    hidden = workbook.create_sheet("Intern")
    hidden["A1"] = "intern"
    hidden.sheet_state = "hidden"

    formulas = workbook.create_sheet("Formeln")
    formulas["A1"] = "Summe"
    formulas["A2"] = "=1+1"  # openpyxl never computes it, so no cached value exists

    workbook.save(path)
    return path


def api_error(cls: type, status: int) -> Exception:
    request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


class Reply(SimpleNamespace):
    """A scripted answer with optional edge cases: `refusal`, `finish_reason`."""

    def __init__(
        self, content: str | None, finish_reason: str = "stop", refusal: str | None = None
    ):
        super().__init__(content=content, finish_reason=finish_reason, refusal=refusal)


class FakeCompletions:
    def __init__(self, text, delay, errors, responses, responder):
        self.text, self.delay, self.errors = text, delay, errors
        self.responses = responses  # one scripted answer per call, then `text`
        self.responder = responder  # or: callable(kwargs) -> answer, for every call
        self.calls = 0
        self.requests: list[dict] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def create(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1

        if self.responder is not None:
            answer = self.responder(kwargs)
        elif self.responses:
            answer = self.responses.pop(0)
        else:
            answer = self.text
        if isinstance(answer, BaseException):
            raise answer
        reply = answer if isinstance(answer, Reply) else Reply(answer)
        message = SimpleNamespace(content=reply.content, refusal=reply.refusal)
        choice = SimpleNamespace(message=message, finish_reason=reply.finish_reason)
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
        return SimpleNamespace(choices=[choice], usage=usage)


class FakeEmbeddings:
    """`embeddings.create`: one vector of `dim` floats per input, or `vectors(texts)`."""

    def __init__(self, dim, errors, vectors):
        self.dim, self.errors, self.vectors = dim, errors, vectors
        self.calls = 0
        self.inputs: list[list[str]] = []

    async def create(self, *, model, input):
        self.calls += 1
        self.inputs.append(list(input))
        if self.errors:
            raise self.errors.pop(0)
        vectors = (
            self.vectors(input)
            if self.vectors
            else [[float(n + 1)] * self.dim for n in range(len(input))]
        )
        data = [SimpleNamespace(embedding=v, index=i) for i, v in enumerate(vectors)]
        return SimpleNamespace(data=data, model=model)


class FakeClient:
    """Stands in for AsyncOpenAI: `chat.completions.create` and `embeddings.create`.

    Answers are `text` by default; `responses` scripts one answer per call (a string, a
    `Reply`, or an exception to raise); `responder(kwargs)` computes the answer per call.
    Embeddings: `embedding_dim` floats per input, `embedding_errors` raised first,
    `vectors(texts)` overrides the vectors (for count and dimension failures).
    """

    def __init__(
        self,
        text: str = "# Seite\n\nGescannter Inhalt",
        delay: float = 0.0,
        errors=(),
        responses=(),
        responder=None,
        embedding_dim: int = 4,
        embedding_errors=(),
        vectors=None,
    ):
        self.completions = FakeCompletions(text, delay, list(errors), list(responses), responder)
        self.chat = SimpleNamespace(completions=self.completions)
        self.embeddings = FakeEmbeddings(embedding_dim, list(embedding_errors), vectors)


def make_document(
    name: str = "doc.pdf",
    pages: list[str] | None = None,
    *,
    ocr_pages: tuple[int, ...] = (),
    status: DocStatus = DocStatus.OK,
    doc_id: str | None = None,
    warnings: list[str] | None = None,
) -> Document:
    """A parsed Document built directly (no file), for agent tests."""
    pages = [SENTENCE] if pages is None else pages
    return Document(
        doc_id=doc_id or f"id-{name}",
        filename=name,
        relative_path=name,
        type=DocType.TEXT_PDF,
        status=status,
        pages=[
            Page(
                page_no=n,
                kind=PageKind.OCR if n in ocr_pages else PageKind.TEXT,
                text=text,
                needs_review=n in ocr_pages,
            )
            for n, text in enumerate(pages, start=1)
        ],
        warnings=warnings or [],
    )


def entry(value, page: int = 1, evidence: str = "", confidence: str = "high") -> dict:
    return {"value": value, "source_page": page, "evidence": evidence, "confidence": confidence}


def item(page: int = 1, evidence: str = "", confidence: str = "high", **values) -> dict:
    """One list entry: item columns plus provenance."""
    return values | {"source_page": page, "evidence": evidence, "confidence": confidence}


DOKUMENT = {"klasse_code": "bescheid", "titel": "Testdokument", "dokument_datum": "2026-02-03"}


def answer(dokument: dict | None = None, **fields) -> str:
    """Agent JSON for tests/fixtures/schema_small.json; scalars not given are null, the
    list `steuerattribut` defaults to [] (items need art, stichtag, betrag_eur)."""
    names = ("company_name", "legal_form", "employees", "annual_revenue_eur")
    payload = {name: fields.get(name) for name in names}
    payload["steuerattribut"] = [
        {"art": None, "stichtag": None, "betrag_eur": None} | i
        for i in fields.get("steuerattribut", [])
    ]
    payload["dokument"] = DOKUMENT if dokument is None else dokument
    return json.dumps(payload)
