"""Semantic checks on structurally valid agent output: pages, evidence, numbers, confidence.

No value without a source: a field citing a page that does not exist is dropped. Evidence
that cannot be found keeps its value (OCR noise breaks exact matches) but is capped and
flagged, so the advisor decides.
"""

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.models.document import Page, PageKind, table_to_markdown
from app.models.extraction import Confidence

_MARKUP = re.compile(r"[|*_#`\\]|-{3,}")
_WHITESPACE = re.compile(r"\s+")


@dataclass
class VerifiedValue:
    value: Any
    source_page: int
    evidence: str
    confidence: float
    needs_review: bool


def normalize_for_match(text: str) -> str:
    """Unicode-normalized, case-insensitive, markup-free, whitespace-collapsed."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return _WHITESPACE.sub(" ", _MARKUP.sub(" ", text)).strip()


def page_search_text(page: Page) -> str:
    parts = [page.text] + [table_to_markdown(t) for t in page.tables]
    return normalize_for_match("\n".join(parts))


def page_errors(entries: dict[str, Any], page_nos: set[int]) -> list[str]:
    """Citation errors for the repair feedback. `entries` maps a field name to its entry,
    None, or (list fields) a list of item entries."""
    valid = ", ".join(str(n) for n in sorted(page_nos))
    errors = []
    for name, entry in entries.items():
        located = (
            [(f"{name}[{i}]", e) for i, e in enumerate(entry)]
            if isinstance(entry, list)
            else [(name, entry)]
        )
        errors += [
            f"{path}.source_page: page {e.source_page} does not exist (valid pages: {valid})"
            for path, e in located
            if e is not None and e.source_page not in page_nos
        ]
    return errors


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    return True


def verify_value(
    name: str,
    value: Any,
    source_page: int,
    evidence: str,
    confidence: Confidence,
    pages: dict[int, Page],
    settings: Settings,
) -> tuple[VerifiedValue | None, list[str]]:
    """Return the checked value, or None and the warning when the field must be dropped."""
    if not _finite(value):
        return None, [f"{name}: dropped, value {value} is not a finite number"]
    if source_page not in pages:
        return None, [f"{name}: dropped, cited page {source_page} does not exist (no source)"]

    warnings: list[str] = []
    needle = normalize_for_match(evidence)
    verified = bool(needle) and needle in page_search_text(pages[source_page])
    if needle and not verified:
        found_on = next(
            (n for n, page in sorted(pages.items()) if needle in page_search_text(page)), None
        )
        if found_on is not None:
            warnings.append(f"{name}: evidence found on page {found_on}, not {source_page}")
            source_page, verified = found_on, True
    if not verified:
        warnings.append(f"{name}: evidence not found in the document, needs review")

    score = {
        Confidence.HIGH: settings.confidence_high,
        Confidence.MEDIUM: settings.confidence_medium,
        Confidence.LOW: settings.confidence_low,
    }[confidence]
    ocr = pages[source_page].kind is PageKind.OCR
    if ocr:
        score = min(score, settings.confidence_ocr_cap)
    if not verified:
        score = min(score, settings.confidence_unverified_cap)

    return (
        VerifiedValue(
            value=value,
            source_page=source_page,
            evidence=evidence[: settings.evidence_max_chars],
            confidence=score,
            needs_review=ocr or not verified or confidence is Confidence.LOW,
        ),
        warnings,
    )
