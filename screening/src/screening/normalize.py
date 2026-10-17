"""Text normalization and content hashing for change detection."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_WS = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Stable form of a document text: NFC, unified whitespace, no trailing spaces.

    Cosmetic changes (extra spaces, line ending style) must not count as a new version.
    """
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
