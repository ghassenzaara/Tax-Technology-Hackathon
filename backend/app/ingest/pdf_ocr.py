"""Scanned pages: render to PNG in memory, transcribe to markdown with GPT vision.

This is only the first of the two scan steps from the brief: transcription, no extraction.
Every result page is kind OCR with `needs_review` set. Failures become page warnings.
"""

import asyncio
import base64
import io
import logging
import threading
from pathlib import Path

import pypdfium2 as pdfium
from openai import AsyncOpenAI

from app.config import Settings
from app.llm import call_with_retry, token_usage
from app.models.document import Page, PageKind, TokenUsage

log = logging.getLogger(__name__)

OCR_SYSTEM_PROMPT = """\
You transcribe scanned pages of German due diligence documents into Markdown.
Rules:
- Transcribe the page faithfully and completely, in its original language. Do not translate.
- Keep the reading order. Use Markdown headings only where the page has headings.
- Render tables as Markdown tables with the same rows and columns.
- Copy numbers, dates and amounts exactly as printed, including German formatting \
(for example 1.234,50 EUR or 31.12.2023).
- Do not summarize, interpret, correct or add anything.
- Mark unreadable parts as [unleserlich].
- Output only the transcription, without comments or code fences."""

MISSING_KEY_WARNING = "OCR skipped: OPENAI_API_KEY is not set"

_PDFIUM_LOCK = threading.Lock()  # pypdfium2 is not thread-safe


async def transcribe_pages(
    path: Path,
    page_nos: list[int],
    *,
    client: AsyncOpenAI | None,
    settings: Settings,
    slots: asyncio.Semaphore,
) -> tuple[list[Page], TokenUsage]:
    """OCR the given pages of one PDF concurrently. Returns pages sorted by number."""
    if client is None:
        return [_ocr_page(n, warning=MISSING_KEY_WARNING) for n in page_nos], TokenUsage()

    cap = settings.ocr_page_cap
    selected, skipped = page_nos[:cap], page_nos[cap:]
    pages = [
        _ocr_page(n, warning=f"OCR skipped: cap of {cap} scanned pages reached") for n in skipped
    ]

    try:
        images, render_errors = await asyncio.to_thread(
            render_pages_png, path, selected, settings.ocr_dpi
        )
    except Exception as exc:
        pages += [_ocr_page(n, warning=f"page render failed: {exc}") for n in selected]
        return sorted(pages, key=lambda p: p.page_no), TokenUsage()

    pages += [_ocr_page(n, warning=message) for n, message in render_errors.items()]
    results = await asyncio.gather(
        *(_transcribe_page(n, png, client, settings, slots) for n, png in images.items())
    )
    usage = TokenUsage()
    for page, tokens in results:
        pages.append(page)
        usage += tokens
    return sorted(pages, key=lambda p: p.page_no), usage


def render_pages_png(
    path: Path, page_nos: list[int], dpi: int
) -> tuple[dict[int, bytes], dict[int, str]]:
    """Render pages to PNG bytes in memory. Returns (images, errors) keyed by page number."""
    images: dict[int, bytes] = {}
    errors: dict[int, str] = {}
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(path)
        try:
            for page_no in page_nos:
                try:
                    page = pdf[page_no - 1]
                    try:
                        buffer = io.BytesIO()
                        page.render(scale=dpi / 72).to_pil().save(buffer, format="PNG")
                        images[page_no] = buffer.getvalue()
                    finally:
                        page.close()
                except Exception as exc:
                    errors[page_no] = f"page render failed: {exc}"
        finally:
            pdf.close()
    return images, errors


async def transcribe_image(
    png: bytes, *, client: AsyncOpenAI, settings: Settings, slots: asyncio.Semaphore
) -> tuple[str, TokenUsage]:
    """One vision call with retry on transient errors. Raises when retries are exhausted."""
    image_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    messages = [
        {"role": "system", "content": OCR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Transcribe this page."},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]
    response = await call_with_retry(
        lambda: client.chat.completions.create(model=settings.openai_model, messages=messages),
        slots=slots,
        max_attempts=settings.ocr_max_attempts,
        backoff_min_s=settings.ocr_backoff_min_s,
        backoff_max_s=settings.ocr_backoff_max_s,
    )
    return (response.choices[0].message.content or "").strip(), token_usage(response)


async def _transcribe_page(
    page_no: int, png: bytes, client: AsyncOpenAI, settings: Settings, slots: asyncio.Semaphore
) -> tuple[Page, TokenUsage]:
    try:
        text, tokens = await transcribe_image(png, client=client, settings=settings, slots=slots)
    except Exception as exc:
        log.warning("OCR failed for page %d: %s", page_no, exc)
        return _ocr_page(page_no, warning=f"OCR failed: {type(exc).__name__}: {exc}"), TokenUsage()
    return _ocr_page(page_no, text=text), tokens


def _ocr_page(page_no: int, text: str = "", warning: str | None = None) -> Page:
    return Page(
        page_no=page_no,
        kind=PageKind.OCR,
        text=text,
        warnings=[warning] if warning else [],
        needs_review=True,
    )
