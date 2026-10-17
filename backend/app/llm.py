"""Shared OpenAI helpers: client factory, the retrying call, structured output, embeddings.

SDK retries are off; tenacity retries instead, and the semaphore is held only while a
request is in flight, so a request in backoff never blocks other documents.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError
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

REPAIR_INSTRUCTION = """Your previous answer was invalid. Errors:
{errors}

Return the corrected complete result. Follow the same rules."""

# Transient errors worth retrying. APITimeoutError is a subclass of APIConnectionError.
RETRYABLE_ERRORS = (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError)


class InvalidOutput(Exception):
    """The model output stayed invalid after the repair retry."""


class EmbeddingError(RuntimeError):
    """An embedding batch came back incomplete or with the wrong dimension."""


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


def read_structured(
    response: Any, model: type[BaseModel]
) -> tuple[str | None, BaseModel | None, list[str]]:
    """(raw content, validated result or None, errors): refusal, cut-off, empty, JSON, schema."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return None, None, ["empty response"]
    choice, message = choices[0], choices[0].message
    refusal = getattr(message, "refusal", None)
    if refusal:
        return None, None, [f"model refused: {refusal}"]
    content = message.content
    if getattr(choice, "finish_reason", None) == "length":
        return content, None, ["output was cut off at the length limit"]
    if not content or not content.strip():
        return content, None, ["empty response"]
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        return content, None, [f"not valid JSON: {exc}"]
    try:
        return content, model.model_validate(data), []
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(part) for part in e['loc']) or '(root)'}: {e['msg']}"
            for e in exc.errors()
        ]
        return content, None, errors


async def ask_structured(
    client: AsyncOpenAI,
    settings: Settings,
    slots: asyncio.Semaphore,
    messages: list[dict[str, Any]],
    *,
    name: str,
    schema: dict[str, Any],
    model: type[BaseModel],
    reasoning_effort: str,
    check: Callable[[BaseModel], list[str]] | None = None,
) -> tuple[BaseModel, TokenUsage, bool]:
    """Strict json_schema call with one repair retry: (result, tokens, repair used).

    `check` adds semantic errors to a structurally valid result. Raises InvalidOutput when
    the repaired answer is still invalid; transient errors are retried by call_with_retry."""
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }

    async def ask(conversation: list[dict[str, Any]]) -> Any:
        return await call_with_retry(
            lambda: client.chat.completions.create(
                model=settings.openai_model,
                messages=conversation,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
            ),
            slots=slots,
            max_attempts=settings.agent_max_attempts,
            backoff_min_s=settings.agent_backoff_min_s,
            backoff_max_s=settings.agent_backoff_max_s,
        )

    def validate(response: Any) -> tuple[str | None, BaseModel | None, list[str]]:
        content, parsed, errors = read_structured(response, model)
        if parsed is not None and check is not None:
            errors = check(parsed)
        return content, parsed if not errors else None, errors

    response = await ask(messages)
    tokens = token_usage(response)
    content, parsed, errors = validate(response)
    if parsed is not None:
        return parsed, tokens, False
    repair = [
        *messages,
        {"role": "assistant", "content": content or "(no output)"},
        {
            "role": "user",
            "content": REPAIR_INSTRUCTION.format(errors="\n".join(f"- {e}" for e in errors)),
        },
    ]
    response = await ask(repair)
    tokens += token_usage(response)
    _, parsed, errors = validate(response)
    if parsed is None:
        raise InvalidOutput("; ".join(errors))
    return parsed, tokens, True


async def embed_texts(
    client: AsyncOpenAI, settings: Settings, slots: asyncio.Semaphore, texts: list[str]
) -> list[list[float]]:
    """Vectors in input order, in batches; checks count, order and dimension of each batch."""
    size = settings.embedding_batch_size
    batches = [texts[i : i + size] for i in range(0, len(texts), size)]
    results = await asyncio.gather(*(_embed_batch(client, settings, slots, b) for b in batches))
    return [vector for batch in results for vector in batch]


async def _embed_batch(
    client: AsyncOpenAI, settings: Settings, slots: asyncio.Semaphore, texts: list[str]
) -> list[list[float]]:
    response = await call_with_retry(  # transient errors are retried here
        lambda: client.embeddings.create(model=settings.embedding_model, input=texts),
        slots=slots,
        max_attempts=settings.agent_max_attempts,
        backoff_min_s=settings.agent_backoff_min_s,
        backoff_max_s=settings.agent_backoff_max_s,
    )
    data = sorted(getattr(response, "data", None) or [], key=lambda d: d.index)
    if [d.index for d in data] != list(range(len(texts))):
        raise EmbeddingError(f"expected {len(texts)} vectors, got {len(data)}")
    for d in data:
        if len(d.embedding) != settings.embedding_dimension:
            raise EmbeddingError(
                f"vector dimension {len(d.embedding)}, expected {settings.embedding_dimension}"
            )
    return [list(d.embedding) for d in data]
