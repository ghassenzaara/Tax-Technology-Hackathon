"""Test file builders and a fake OpenAI client. Fixtures are generated at run time;
real fixtures come from a teammate later. No network is ever used."""

import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx2  # openai 3.x HTTP layer; its error classes expect httpx2 responses
import openpyxl
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

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


class FakeCompletions:
    def __init__(self, text: str, delay: float, errors: list[Exception]):
        self.text, self.delay, self.errors = text, delay, errors
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        message = SimpleNamespace(content=self.text)
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class FakeClient:
    """Stands in for AsyncOpenAI: only `chat.completions.create` is used."""

    def __init__(self, text: str = "# Seite\n\nGescannter Inhalt", delay: float = 0.0, errors=()):
        self.completions = FakeCompletions(text, delay, list(errors))
        self.chat = SimpleNamespace(completions=self.completions)
