"""Shared OpenAI helpers for OCR and extraction: client factory and the retrying call.

SDK retries are off; tenacity retries instead, and the semaphore is held only while a
request is in flight, so a request in backoff never blocks other documents.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import openai
from openai import AsyncOpenAI
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.config import Settings
from app.models.document import TokenUsage

log = logging.getLogger(__name__)

# Transient errors worth retrying. APITimeoutError is a subclass of APIConnectionError.
RETRYABLE_ERRORS = (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError)


def make_client(settings: Settings) -> AsyncOpenAI | None:
    """OpenAI client, or None when no key is configured (text-only runs still work)."""
    if settings.openai_api_key is None:
        return None
    return AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.openai_timeout_s,
        max_retries=0,
    )


async def call_with_retry(
    request: Callable[[], Awaitable[Any]],
    *,
    slots: asyncio.Semaphore,
    max_attempts: int,
    backoff_min_s: float,
    backoff_max_s: float,
) -> Any:
    """Run `request` under `slots`, retrying transient errors. Raises when retries run out."""
    retrying = AsyncRetrying(
        retry=retry_if_exception_type(RETRYABLE_ERRORS),
        wait=wait_random_exponential(min=backoff_min_s, max=backoff_max_s),
        stop=stop_after_attempt(max_attempts),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            async with slots:  # hold a slot only while the request is in flight
                response = await request()
    return response


def token_usage(response: Any) -> TokenUsage:
    usage = getattr(response, "usage", None)
    return TokenUsage(
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )
