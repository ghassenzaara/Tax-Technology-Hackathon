"""Request context (pure ASGI): request id, one log line per request, no-store caching and
the total size limit of the upload body."""

import logging
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import ErrorCode, UploadTooLarge, error_response

log = logging.getLogger("app.api")


class RequestContext:
    def __init__(self, app: ASGIApp, *, upload_path: str, upload_limit: int):
        self.app = app
        self.upload_path = upload_path
        self.upload_limit = upload_limit  # bytes, multipart overhead included

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
            if scope["method"] == "POST" and scope["path"] == self.upload_path:
                if self._declared_length(scope) > self.upload_limit:
                    response = error_response(
                        413, ErrorCode.UPLOAD_TOO_LARGE, self._too_large_message()
                    )
                    await response(scope, receive, send_with_headers)
                    return
                receive = self._limited(receive)
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

    @staticmethod
    def _declared_length(scope: Scope) -> int:
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return 0
        return 0

    def _too_large_message(self) -> str:
        return f"upload larger than {self.upload_limit // (1024 * 1024)} MB"

    def _limited(self, receive: Receive) -> Receive:
        """Count body bytes as they arrive (also without a Content-Length header)."""
        received = 0

        async def wrapped() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.upload_limit:
                    raise UploadTooLarge(self._too_large_message())
            return message

        return wrapped
