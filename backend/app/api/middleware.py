"""Pure ASGI middleware. Effective order, outermost first:

    RequestContext -> CORS -> RequestGuard -> FastAPI error handlers and routes

RequestContext gives every response (errors included) a request id and `Cache-Control:
no-store` and writes one log line. RequestGuard sits inside CORS, so its early answers
(upload too large) and the JSON 500 for unexpected exceptions carry the CORS headers a
browser needs to read them."""

import logging
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import ErrorCode, UploadTooLarge, error_response, internal_error

log = logging.getLogger("app.api")


class RequestContext:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex[:12]
        scope.setdefault("state", {})["request_id"] = request_id
        start = perf_counter()
        status = 500

        async def send_with_headers(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"  # drafts and profiles are confidential
                headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:  # path without query string; never bodies or document text
            log.info(
                "request id=%s %s %s status=%d %.3fs",
                request_id,
                scope["method"],
                scope["path"],
                status,
                perf_counter() - start,
            )


class RequestGuard:
    """Body size limits of the upload paths, and the error boundary: an exception no
    handler answered becomes the JSON 500 (with the request id as error id), unless the
    response has started already (then nothing more is sent)."""

    def __init__(self, app: ASGIApp, *, limits: dict[str, int]):
        self.app = app
        self.limits = limits  # POST path -> bytes, multipart overhead included

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        limit = self.limits.get(scope["path"]) if scope["method"] == "POST" else None
        if limit is not None:
            if _declared_length(scope) > limit:
                response = error_response(413, ErrorCode.UPLOAD_TOO_LARGE, _too_large(limit))
                await response(scope, receive, send)
                return
            receive = _limited(receive, limit)
        try:
            await self.app(scope, receive, tracking)
        except Exception as exc:
            if started:  # never a second response
                log.error("error after the response started: %s", type(exc).__name__)
                return
            if isinstance(exc, UploadTooLarge):
                response = error_response(413, ErrorCode.UPLOAD_TOO_LARGE, str(exc))
            else:
                response = internal_error(scope, exc)
            await response(scope, receive, send)


def _declared_length(scope: Scope) -> int:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return 0
    return 0


def _too_large(limit: int) -> str:
    return f"upload larger than {limit // (1024 * 1024)} MB"


def _limited(receive: Receive, limit: int) -> Receive:
    """Count body bytes as they arrive (also without a Content-Length header)."""
    received = 0

    async def wrapped() -> Message:
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                raise UploadTooLarge(_too_large(limit))
        return message

    return wrapped
