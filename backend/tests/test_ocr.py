import asyncio

import openai
import pytest

from app.ingest.pdf_ocr import transcribe_image, transcribe_pages
from tests.helpers import FakeClient, api_error, make_pdf


async def test_retries_rate_limit_then_succeeds(settings):
    client = FakeClient(errors=[api_error(openai.RateLimitError, 429)])

    text, tokens = await transcribe_image(
        b"png", client=client, settings=settings, slots=asyncio.Semaphore(1)
    )

    assert client.completions.calls == 2
    assert "Gescannter Inhalt" in text and tokens.output_tokens == 20


async def test_non_retryable_error_is_not_retried(settings):
    client = FakeClient(errors=[api_error(openai.AuthenticationError, 401)])

    with pytest.raises(openai.AuthenticationError):
        await transcribe_image(b"png", client=client, settings=settings, slots=asyncio.Semaphore(1))

    assert client.completions.calls == 1


async def test_page_cap_flags_remaining_pages(tmp_path, settings):
    path = make_pdf(tmp_path / "scan.pdf", [None, None, None])
    capped = settings.model_copy(update={"ocr_page_cap": 2})

    pages, _ = await transcribe_pages(
        path, [1, 2, 3], client=FakeClient(), settings=capped, slots=asyncio.Semaphore(5)
    )

    assert [p.page_no for p in pages] == [1, 2, 3]
    assert pages[0].text and pages[1].text
    assert pages[2].text == "" and "cap" in pages[2].warnings[0]
