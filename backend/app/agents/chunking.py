"""Split long documents on page boundaries. Most documents fit in one call and never chunk.

Token counts are estimated as characters / chars_per_token; no tokenizer dependency.
TODO: switch to a real tokenizer only if the estimate proves too coarse.
"""

import math
from dataclasses import dataclass

from app.config import Settings
from app.models.document import Document, Page


@dataclass
class Chunk:
    index: int
    pages: list[Page]
    markdown: str  # keeps the original page markers, so citations keep their page numbers


def estimate_tokens(text: str, chars_per_token: float) -> int:
    return math.ceil(len(text) / chars_per_token)


def chunk_document(doc: Document, settings: Settings) -> tuple[list[Chunk], list[str]]:
    """Return the chunks to send and warnings (for pages larger than the target)."""
    cpt = settings.chars_per_token
    full = doc.to_markdown()
    if estimate_tokens(full, cpt) <= settings.chunk_trigger_tokens:
        return [Chunk(0, list(doc.pages), full)], []

    target, overlap = settings.chunk_target_tokens, settings.chunk_overlap_pages
    size = {p.page_no: estimate_tokens(_render(doc, [p]), cpt) for p in doc.pages}
    groups: list[list[Page]] = []
    warnings: list[str] = []
    current: list[Page] = []

    for page in doc.pages:
        if size[page.page_no] > target:  # never cut mid-page: send it alone
            if current:
                groups.append(current)
            groups.append([page])
            warnings.append(
                f"page {page.page_no} is larger than the chunk target "
                f"({size[page.page_no]} > {target} estimated tokens); sent alone"
            )
            current = []
            continue
        if current and sum(size[p.page_no] for p in current) + size[page.page_no] > target:
            groups.append(current)
            current = current[-overlap:] if overlap else []
            while current and sum(size[p.page_no] for p in current) + size[page.page_no] > target:
                current = current[1:]
        current.append(page)
    if current:
        groups.append(current)

    return [Chunk(i, pages, _render(doc, pages)) for i, pages in enumerate(groups)], warnings


def _render(doc: Document, pages: list[Page]) -> str:
    return doc.model_copy(update={"pages": pages}).to_markdown()
