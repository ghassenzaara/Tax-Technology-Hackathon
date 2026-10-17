"""One error shape for every response: a machine-readable code, a human message, optional
details (per field or per file). Server errors carry an error id that also appears in the
server log; no response ever contains a stack trace, a server path or document text."""

import logging
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from app.db.repository import (
    AlreadySentError,
    BusyError,
    DuplicateLawError,
    NotFoundError,
    PublishError,
    RepositoryError,
    RunActiveError,
    SendPendingError,
    StaleDraftError,
    StaleProfileError,
    StateError,
    UnsupportedError,
)
from app.mail.provider import MailConfigError, MailError
from app.profile.review import DecisionError
from app.runs.jobs import LLMUnavailableError
from app.services.email import SendFailed, SendUncertain

log = logging.getLogger("app.api")


class ErrorCode(StrEnum):
    VALIDATION = "validation"  # 422: invalid body, decision or change; details per field
    BAD_REQUEST = "bad_request"  # 400: malformed request (for example a broken multipart body)
    EMPTY_UPLOAD = "empty_upload"  # 400: no file sent, or every file rejected (details per file)
    UPLOAD_TOO_LARGE = "upload_too_large"  # 413: request body over the total upload limit
    NOT_FOUND = "not_found"  # 404: unknown company, run or path
    WRONG_STATE = "wrong_state"  # 409: the project state does not allow the call
    STALE_DRAFT = "stale_draft"  # 409: the draft changed; reload the draft
    STALE_PROFILE = "stale_profile"  # 409: the profile version changed; reload the profile
    BUSY = "busy"  # 409: a step or background job of the company (or this law) is active
    RUN_ACTIVE = "run_active"  # 409: a run of the company is queued or running
    DUPLICATE = "duplicate"  # 409: the same legal source is stored already (use replace)
    SEND_PENDING = "send_pending"  # 409: an earlier send is unresolved (see outreach_id)
    ALREADY_SENT = "already_sent"  # 409: the alert's email was sent (see outreach_id)
    SEND_FAILED = "send_failed"  # 502: the mail provider rejected it; a new send may follow
    MAIL_UNAVAILABLE = "mail_unavailable"  # 503: mail provider not configured
    LLM_UNAVAILABLE = "llm_unavailable"  # 503: the server has no OPENAI_API_KEY
    PUBLISH_FAILED = "publish_failed"  # 422: the profile could not be written (details in text)
    NOT_SUPPORTED = "not_supported"  # 501: the configured database cannot do this
    DATABASE_UNAVAILABLE = "database_unavailable"  # 503
    METHOD_NOT_ALLOWED = "method_not_allowed"  # 405
    INTERNAL_ERROR = "internal_error"  # 500: see error_id in the server log


class ErrorDetail(BaseModel):
    field: str | None = None  # profile field, request field or uploaded relative path
    key: dict[str, Any] | None = None  # list rows: the row's key values
    message: str


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: list[ErrorDetail] = []
    error_id: str | None = None  # server errors: the id in the server log
    outreach_id: int | None = None  # email errors: the durable send attempt


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: ErrorCode,
        message: str,
        details: list[ErrorDetail] | None = None,
    ):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or []


class UploadTooLarge(Exception):
    """The request body passed the total upload limit while it was being read."""


# Most specific first: StaleDraftError and the others are RepositoryError subclasses.
REPOSITORY_ERRORS: list[tuple[type[RepositoryError], int, ErrorCode]] = [
    (NotFoundError, 404, ErrorCode.NOT_FOUND),
    (SendPendingError, 409, ErrorCode.SEND_PENDING),
    (AlreadySentError, 409, ErrorCode.ALREADY_SENT),
    (DuplicateLawError, 409, ErrorCode.DUPLICATE),
    (StaleDraftError, 409, ErrorCode.STALE_DRAFT),
    (StaleProfileError, 409, ErrorCode.STALE_PROFILE),
    (BusyError, 409, ErrorCode.BUSY),
    (RunActiveError, 409, ErrorCode.RUN_ACTIVE),
    (StateError, 409, ErrorCode.WRONG_STATE),
    (PublishError, 422, ErrorCode.PUBLISH_FAILED),
    (UnsupportedError, 501, ErrorCode.NOT_SUPPORTED),
]

# Shown in the OpenAPI file for every route.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorBody, "description": text}
    for status, text in (
        (400, "bad_request, empty_upload"),
        (404, "not_found"),
        (
            409,
            "wrong_state, stale_draft, stale_profile, busy, run_active, duplicate, "
            "send_pending, already_sent",
        ),
        (413, "upload_too_large"),
        (422, "validation (details per field), publish_failed"),
        (500, "internal_error (with error_id)"),
        (501, "not_supported"),
        (502, "send_failed (with outreach_id)"),
        (503, "database_unavailable, mail_unavailable, llm_unavailable"),
    )
}


def error_response(
    status: int,
    code: ErrorCode,
    message: str,
    details: list[ErrorDetail] | None = None,
    error_id: str | None = None,
    outreach_id: int | None = None,
) -> JSONResponse:
    body = ErrorBody(
        code=code,
        message=message,
        details=details or [],
        error_id=error_id,
        outreach_id=outreach_id,
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(DecisionError)  # also UpdateError: every problem per field
    async def decision_error(request: Request, exc: DecisionError) -> JSONResponse:
        details = [ErrorDetail(field=e.field, key=e.key, message=e.message) for e in exc.errors]
        return error_response(
            422, ErrorCode.VALIDATION, "refused, nothing changed: see details", details
        )

    @app.exception_handler(RepositoryError)
    async def repository_error(request: Request, exc: RepositoryError) -> JSONResponse:
        for cls, status, code in REPOSITORY_ERRORS:
            if isinstance(exc, cls):
                outreach_id = getattr(exc, "outreach_id", None)
                return error_response(status, code, str(exc), outreach_id=outreach_id)
        return internal_error(request.scope, exc)

    @app.exception_handler(MailError)  # typed: never parsed from the message text
    async def mail_error(request: Request, exc: MailError) -> JSONResponse:
        if isinstance(exc, SendUncertain):
            return error_response(
                409, ErrorCode.SEND_PENDING, str(exc), outreach_id=exc.outreach_id
            )
        if isinstance(exc, SendFailed):
            return error_response(502, ErrorCode.SEND_FAILED, str(exc), outreach_id=exc.outreach_id)
        if isinstance(exc, MailConfigError):
            return error_response(503, ErrorCode.MAIL_UNAVAILABLE, str(exc))
        return error_response(502, ErrorCode.SEND_FAILED, "mail provider error")

    @app.exception_handler(LLMUnavailableError)
    async def llm_unavailable(request: Request, exc: LLMUnavailableError) -> JSONResponse:
        return error_response(503, ErrorCode.LLM_UNAVAILABLE, str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            ErrorDetail(
                field=".".join(str(p) for p in e.get("loc", ())[1:]) or None, message=e["msg"]
            )
            for e in exc.errors()  # never echo the input: it may hold document values
        ]
        return error_response(422, ErrorCode.VALIDATION, "invalid request", details)

    @app.exception_handler(UploadTooLarge)
    async def too_large(request: Request, exc: UploadTooLarge) -> JSONResponse:
        return error_response(413, ErrorCode.UPLOAD_TOO_LARGE, str(exc))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = {
            400: ErrorCode.BAD_REQUEST,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.METHOD_NOT_ALLOWED,
        }.get(exc.status_code, ErrorCode.BAD_REQUEST)
        message = exc.detail if isinstance(exc.detail, str) else code.value
        return error_response(exc.status_code, code, message)

    # Unexpected exceptions are answered by the RequestGuard middleware (inside CORS and
    # the request context), not by a handler here: Starlette would run that handler
    # outside every middleware, without CORS, request id or no-store headers.


def internal_error(scope, exc: Exception) -> JSONResponse:
    error_id = scope.get("state", {}).get("request_id")
    log.error("error_id=%s %s: %s", error_id, type(exc).__name__, exc, exc_info=exc)
    return error_response(
        500,
        ErrorCode.INTERNAL_ERROR,
        "internal server error; quote the error id when reporting it",
        error_id=error_id,
    )
