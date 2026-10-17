"""Common interface for source fetchers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from screening.http import PoliteClient


@dataclass(frozen=True)
class SourceItem:
    """One entry in a source listing, before its full text is fetched."""

    source: str  # fetcher name, for example "bmf"
    key: str  # official id or canonical URL; stable across runs
    title: str
    url: str
    source_type: str  # contract vocabulary: gesetzentwurf, bmf_schreiben, gesetz, urteil
    source_name: str  # for example "BMF"
    published_at: date | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchedDocument:
    text: str  # extracted plain text, used for hashing and sections
    raw: bytes  # original bytes, kept as snapshot for traceability
    content_type: str
    extra: dict[str, str] = field(default_factory=dict)


class Fetcher(Protocol):
    name: str

    def list_items(self, client: PoliteClient, limit: int | None = None) -> list[SourceItem]: ...

    def fetch(self, client: PoliteClient, item: SourceItem) -> FetchedDocument: ...
