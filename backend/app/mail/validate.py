"""Shared validation for extracted contact addresses and advisor-submitted mail."""

import re

from app.config import Settings

_ADDRESS = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)


def address(value: str, limit: int = 254) -> str:
    if not isinstance(value, str) or len(value) > limit or not _ADDRESS.fullmatch(value):
        raise ValueError("recipient must be one valid email address (no spaces or separators)")
    return value


def mail_text(subject: str, body: str, settings: Settings) -> None:
    try:
        subject.encode("utf-8")
        body.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("subject and body must be valid UTF-8 text") from None
    if (
        not subject.strip()
        or len(subject) > settings.mail_subject_max_chars
        or any(c in subject for c in "\r\n\x00")
    ):
        raise ValueError(
            "subject must be one non-empty line, "
            f"at most {settings.mail_subject_max_chars} characters"
        )
    if not body.strip() or len(body) > settings.mail_body_max_chars or "\x00" in body:
        raise ValueError(
            f"body must be non-empty text, at most {settings.mail_body_max_chars} characters"
        )
