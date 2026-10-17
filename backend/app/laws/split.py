"""Step 12, plain code (no LLM): split a parsed law into sections and find their norms.

Cut at heading lines (paragraphs, articles, Randziffern, numbered headings), merge tiny
pieces into the next one, split oversized pieces on paragraph boundaries. Without any
heading, every page becomes a section. Pure boilerplate (table of contents, signature
block, repeated page headers and footers, page numbers) is dropped and reported.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from app.agents.chunking import estimate_tokens
from app.config import Settings
from app.models.document import Document, PageKind, table_to_markdown
from app.models.law import LawSection

# Lines that start a section; group 1 is the reference. A line starting with a norm
# citation ("§ 8c KStG ist ...") is running text, not a heading.
# TODO: extend for other source layouts; the supplied BFH PDF uses sequential margin numbers.
HEADING_PATTERNS = (
    re.compile(
        r"^(§\s*\d+[a-z]?)(?=\s*$|\s+(?!Abs\.|Absatz|Satz|S\.|Nr\.|AO\b|[A-Z][A-Za-z]*G\b)[A-ZÄÖÜ])"
    ),
    re.compile(r"^(Art(?:ikel|\.)\s*\d+[a-z]?)\b"),
    re.compile(r"^(Abschnitt\s+[\dIVX]+)\b"),
    re.compile(r"^((?:Rz\.|Rn\.|Randziffer|Randnummer)\s*\d+)\b"),
    re.compile(r"^([IVX]+\.)\s+[A-ZÄÖÜ]"),
    re.compile(r"^(\d{1,3}(?:\.\d{1,3})*\.?)\s+[A-ZÄÖÜ]"),
)
_NUMBERED = re.compile(r"^[\d.]+$")
_HEADING_MAX_CHARS = 200
_PREAMBLE_REF = "Einleitung"
_BFH_PARAGRAPH = re.compile(r"^(\d{1,3})\s+\S")
_BFH_PARTS = frozenset({"Leitsätze", "Tenor", "Tatbestand", "Entscheidungsgründe"})

_TOC_TITLE = re.compile(r"^(Inhaltsverzeichnis|Inhaltsübersicht|Inhalt)\s*:?$", re.IGNORECASE)
_TOC_LINE = re.compile(r"(\.{3,}|…+)\s*\d+\s*$")
_PAGE_NUMBER = re.compile(r"^(Seite\s+)?\d+(\s*(von|/)\s*\d+)?$", re.IGNORECASE)
_SIGNATURE = re.compile(
    r"^(Im Auftrag|Mit freundlichen Grüßen|Dieses Dokument wurde elektronisch)", re.IGNORECASE
)
_SIGNATURE_MAX_LINES = 8

# Norm citations, normalised to the codes of profil.norm ("§ 8c Abs. 1 Satz 1 KStG").
# TODO: lists of paragraphs ("§§ 8c, 8d KStG") are not split into single norms yet.
_PART = r"(Abs\.|Absatz|S\.|Satz|Nr\.|Nummer|Buchst\.|Buchstabe)\s*(\d+[a-z]?|[a-z])(?!\w)"
NORM_PATTERN = re.compile(
    rf"§\s*(?P<nr>\d+[a-z]?)(?P<parts>(?:\s+{_PART})*)\s+(?P<statute>AO|[A-Z][A-Za-z]{{1,12}}G)\b"
)
_SPELLING = {"Absatz": "Abs.", "Satz": "S.", "Nummer": "Nr.", "Buchstabe": "Buchst."}

Line = tuple[int, str]  # (page number, text); an empty text marks a paragraph break


@dataclass
class SplitResult:
    sections: list[LawSection]
    strategy: str  # headings | pages
    dropped: list[str] = field(default_factory=list)
    norms_found: list[str] = field(default_factory=list)
    norms_unmatched: list[str] = field(default_factory=list)


@dataclass
class _Piece:
    first_ref: str
    last_ref: str
    heading: str | None
    lines: list[Line]

    @property
    def ref(self) -> str:
        return (
            self.first_ref
            if self.first_ref == self.last_ref
            else (f"{self.first_ref} bis {self.last_ref}")
        )

    @property
    def text(self) -> str:
        return _join(self.lines)


def split_law(
    doc: Document, settings: Settings, vocabulary: set[str], law_tax_types: list[str]
) -> SplitResult:
    """Sections in reading order, with pages, norms and tax types."""
    pages, dropped = _clean_pages(doc)
    bfh_ruling = any("ECLI:DE:BFH:" in page.text for page in doc.pages)
    pieces = _cut_at_headings(pages, bfh_ruling=bfh_ruling)
    strategy = "headings"
    if not any(p.heading for p in pieces):
        pieces = [_Piece(f"Seite {n}", f"Seite {n}", None, lines) for n, lines in pages]
        strategy = "pages"
    pieces = _merge_small([p for p in pieces if p.text], settings)
    pieces = [part for p in pieces for part in _split_large(p, settings)]

    sections, found, unmatched = [], [], []
    for nr, piece in enumerate(pieces, start=1):
        norms, missing = find_norms(piece.text, vocabulary)
        found += norms
        unmatched += missing
        used = [p for p, text in piece.lines if text.strip()]
        sections.append(
            LawSection(
                nr=nr,
                section_ref=piece.ref,
                heading=piece.heading,
                page_from=min(used),
                page_to=max(used),
                text=piece.text,
                norms=norms,
                tax_types=section_tax_types(norms + missing, law_tax_types, settings),
            )
        )
    return SplitResult(
        sections=sections,
        strategy=strategy,
        dropped=dropped,
        norms_found=_unique(found),
        norms_unmatched=_unique(unmatched),
    )


def find_norms(text: str, vocabulary: set[str]) -> tuple[list[str], list[str]]:
    """(codes in the vocabulary, normalised codes not in it); the most specific code wins."""
    matched, unmatched = [], []
    for m in NORM_PATTERN.finditer(text):
        parts = [f"{_SPELLING.get(k, k)} {v}" for k, v in re.findall(_PART, m["parts"])]
        codes = [
            " ".join(["§", m["nr"], *parts[:n], m["statute"]]) for n in range(len(parts), -1, -1)
        ]
        code = next((c for c in codes if c in vocabulary), None)
        if code:
            matched.append(code)
        else:
            unmatched.append(codes[0])
    return _unique(matched), [c for c in _unique(unmatched) if c not in matched]


def section_tax_types(norms: list[str], law_tax_types: list[str], settings: Settings) -> list[str]:
    """Tax types from the statutes of the norms; the law's tax types when no norm maps."""
    found = {t for code in norms for t in settings.statute_tax_types.get(code.split()[-1], [])}
    return sorted(found) if found else sorted(law_tax_types)


def _clean_pages(doc: Document) -> tuple[list[tuple[int, list[Line]]], list[str]]:
    """Page lines without boilerplate, plus what was dropped."""
    pages, dropped = [], []
    raw = {page.page_no: _page_lines(page) for page in doc.pages}
    repeated = _repeated_edges(raw)
    for text in sorted(repeated):
        dropped.append(f"page header or footer '{text}'")
    toc_lines = numbers = 0
    for page_no, lines in raw.items():
        content = [t for t in lines if t.strip()]
        if content and _TOC_TITLE.match(content[0].strip()):
            dropped.append(f"table of contents (page {page_no})")
            continue
        if len(content) <= _SIGNATURE_MAX_LINES and any(
            _SIGNATURE.match(t.strip()) for t in content
        ):
            dropped.append(f"signature block (page {page_no})")
            continue
        kept = []
        for text in lines:
            stripped = text.strip()
            if stripped in repeated:
                continue
            if _PAGE_NUMBER.match(stripped):
                numbers += 1
                continue
            if _TOC_LINE.search(stripped):
                toc_lines += 1
                continue
            kept.append((page_no, stripped))
        if any(t for _, t in kept):
            pages.append((page_no, kept))
    if numbers:
        dropped.append(f"page numbers ({numbers} lines)")
    if toc_lines:
        dropped.append(f"table of contents lines ({toc_lines})")
    return pages, dropped


def _page_lines(page) -> list[str]:
    lines = page.text.splitlines()
    if page.kind is not PageKind.SHEET:
        for table in page.tables:
            lines += ["", *table_to_markdown(table).splitlines()]
    return lines


def _repeated_edges(pages: dict[int, list[str]]) -> set[str]:
    """First or last lines that repeat on more than half of the pages (3 pages or more)."""
    if len(pages) < 3:
        return set()
    edges = Counter()
    for lines in pages.values():
        content = [t.strip() for t in lines if t.strip()]
        edges.update({content[0], content[-1]} if content else set())
    return {t for t, n in edges.items() if n > len(pages) / 2 and not _PAGE_NUMBER.match(t)}


def heading_ref(line: str) -> str | None:
    """The section reference when the line is a heading, else None."""
    for pattern in HEADING_PATTERNS:
        m = pattern.match(line)
        if m:
            ref = m.group(1).rstrip(".") if _NUMBERED.match(m.group(1)) else m.group(1)
            return f"Nr. {ref}" if _NUMBERED.match(ref) else " ".join(ref.split())
    return None


def _cut_at_headings(
    pages: list[tuple[int, list[Line]]], *, bfh_ruling: bool = False
) -> list[_Piece]:
    pieces: list[_Piece] = []
    current = _Piece(_PREAMBLE_REF, _PREAMBLE_REF, None, [])
    next_margin = None
    for _, lines in pages:
        for page_no, text in lines:
            if bfh_ruling:
                # BFH margin numbers continue from facts into reasons. Requiring the
                # next number avoids cutting at citations such as "75 Jahre ...".
                ref = text if text in _BFH_PARTS else None
                if text == "Tatbestand":
                    next_margin = 1
                margin = _BFH_PARAGRAPH.match(text)
                if margin and int(margin[1]) == next_margin:
                    ref = f"Rn. {next_margin}"
                    next_margin += 1
            else:
                ref = heading_ref(text)
            if ref is None:
                current.lines.append((page_no, text))
                continue
            pieces.append(current)
            current = _Piece(ref, ref, text[:_HEADING_MAX_CHARS], [(page_no, text)])
    pieces.append(current)
    return pieces


def _tokens(lines: list[Line], settings: Settings) -> int:
    return estimate_tokens(_join(lines), settings.chars_per_token)


def _merge(a: _Piece, b: _Piece) -> _Piece:
    first_page = next((p for p, _ in b.lines), 0)
    lines = [*a.lines, (first_page, ""), *b.lines]
    return _Piece(a.first_ref, b.last_ref, a.heading or b.heading, lines)


def _merge_small(pieces: list[_Piece], settings: Settings) -> list[_Piece]:
    """Tiny pieces join the next one (the last joins the previous) within the maximum."""
    out: list[_Piece] = []
    pending: _Piece | None = None
    for piece in pieces:
        if pending is not None:
            merged = _merge(pending, piece)
            if _tokens(merged.lines, settings) <= settings.section_max_tokens:
                piece = merged
            else:
                out.append(pending)
            pending = None
        if _tokens(piece.lines, settings) < settings.section_min_tokens:
            pending = piece
        else:
            out.append(piece)
    if pending is not None:
        if out and _tokens(_merge(out[-1], pending).lines, settings) <= settings.section_max_tokens:
            out[-1] = _merge(out[-1], pending)
        else:
            out.append(pending)
    return out


def _split_large(piece: _Piece, settings: Settings) -> list[_Piece]:
    """Pieces above the maximum are packed into parts near the target size."""
    if _tokens(piece.lines, settings) <= settings.section_max_tokens:
        return [piece]
    units: list[list[Line]] = []
    for paragraph in _paragraphs(piece.lines):
        if _tokens(paragraph, settings) <= settings.section_max_tokens:
            units.append(paragraph)
            continue
        for page_no, text in paragraph:  # one line per unit, cut when a line is too long
            size = int(settings.section_max_tokens * settings.chars_per_token)
            units += [[(page_no, text[i : i + size])] for i in range(0, len(text), size)]
    parts: list[list[Line]] = []
    for unit in units:
        if parts and _tokens(parts[-1] + unit, settings) <= settings.section_target_tokens:
            parts[-1] += [(unit[0][0], ""), *unit]
        else:
            parts.append(list(unit))
    ref = f"{piece.ref} (Fortsetzung)"
    return [
        _Piece(piece.ref if n == 0 else ref, piece.ref if n == 0 else ref, piece.heading, lines)
        for n, lines in enumerate(parts)
    ]


def _paragraphs(lines: list[Line]) -> list[list[Line]]:
    paragraphs: list[list[Line]] = [[]]
    for page_no, text in lines:
        if text.strip():
            paragraphs[-1].append((page_no, text))
        elif paragraphs[-1]:
            paragraphs.append([])
    return [p for p in paragraphs if p]


def _join(lines: list[Line]) -> str:
    text = "\n".join(t for _, t in lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
