"""The one email template with placeholders, and the law-term check.

Python port of frontend/src/features/email/template.ts and lawTermCheck.ts (same rules, so
the server repeats exactly the check the dialog shows). The email only offers a talk: it
must not name laws, norms, court rulings, Aktenzeichen or words of the client's matched law
changes. Pure functions, no LLM.
"""

import re
from dataclasses import dataclass

from app.models.outreach import (
    DEFAULT_BODY,
    DEFAULT_SUBJECT,
    PLACEHOLDER_GREETING,
    PLACEHOLDER_SIGNATURE,
    PLACEHOLDERS,
)

L = r"[^\W\d_]"  # any letter, umlauts included (JavaScript \p{L})

FIXED_PATTERNS = [
    re.compile(r"§+"),
    re.compile(rf"(?<!{L})(?:Abs|Art|Nr|Rn|Tz)\.\s*\d+", re.IGNORECASE),
    re.compile(
        rf"(?<!{L})(?:BMF|BFH|EuGH|BVerfG|BGBl|BStBl|BT-Drs|Urteil|Urteile|Urteils|Beschluss|"
        r"Beschlusses|Aktenzeichen|Erlass|Erlasses|Richtlinie|Richtlinien|Verordnung|"
        r"Verordnungen|UStG|EStG|KStG|GewStG|AO|AStG|UmwStG|GrStG|ErbStG|UStAE|EStR)"
        rf"(?!{L})",
        re.IGNORECASE,
    ),
    # Aktenzeichen like "V R 99/25" or "I R 0/25".
    re.compile(r"\b[IVXL]{1,4}\s+[A-Z]{1,3}(?:\s+[A-Z]{1,2})?\s+\d{1,4}/\d{2}\b"),
    # Any word containing "gesetz": Jahressteuergesetz, Gesetzentwurf, Gesetzes.
    re.compile(rf"(?<!{L}){L}*gesetz{L}*(?!{L})", re.IGNORECASE),
]

STOP_WORDS = frozenset(
    [
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einer",
        "eines",
        "einem",
        "und",
        "oder",
        "aber",
        "bei",
        "ab",
        "für",
        "fuer",
        "von",
        "vom",
        "zu",
        "zur",
        "zum",
        "im",
        "in",
        "mit",
        "auf",
        "über",
        "ueber",
        "unter",
        "aus",
        "nach",
        "vor",
        "bis",
        "durch",
        "gegen",
        "ohne",
        "um",
        "an",
        "als",
        "auch",
        "nicht",
        "neue",
        "neuer",
        "neues",
        "neuen",
        "änderung",
        "änderungen",
        "anpassung",
        "anpassungen",
        "neuregelung",
        "neuregelungen",
        "entwurf",
        "regelung",
        "regelungen",
        "regeln",
        "pflicht",
        "pflichten",
        "verkehr",
        "fragen",
        "hinweise",
        "ihre",
        "ihr",
        "wir",
        "sie",
        "unternehmen",
        "mandanten",
    ]
)

ACADEMIC_TITLES = re.compile(r"^(dr\.|prof\.|dipl\.-[\wäöü]+\.?)$", re.IGNORECASE)
TOKEN_SPLIT = re.compile(r"[^\w-]+|_+")


@dataclass(frozen=True)
class Contact:
    name: str = ""
    salutation: str | None = None


@dataclass(frozen=True)
class Signer:
    name: str
    title: str | None = None
    firm: str | None = None


@dataclass(frozen=True)
class LawTerms:
    """The words of one law change the check knows: title, tax types, norms."""

    title: str
    tax_types: tuple[str, ...] = ()
    norms: tuple[str, ...] = ()


def build_greeting(contact: Contact) -> str:
    parts = contact.name.strip().split()
    titles = [p for p in parts if ACADEMIC_TITLES.match(p)]
    last_name = parts[-1] if parts else ""
    salutation = (contact.salutation or "").strip().lower()
    name_with_title = " ".join([*titles, last_name])
    if salutation == "herr":
        return f"Sehr geehrter Herr {name_with_title}"
    if salutation == "frau":
        return f"Sehr geehrte Frau {name_with_title}"
    return f"Guten Tag {contact.name.strip()}"


def build_signature(signer: Signer) -> str:
    return "\n".join(p for p in (signer.name, signer.title, signer.firm) if p)


def fill(text: str, contact: Contact, signer: Signer) -> str:
    return text.replace(PLACEHOLDER_GREETING, build_greeting(contact)).replace(
        PLACEHOLDER_SIGNATURE, build_signature(signer)
    )


def unknown_placeholders(text: str) -> list[str]:
    found = dict.fromkeys(re.findall(r"\{[^{}\n]*\}", text))
    return [p for p in found if p not in PLACEHOLDERS]


def _tokens(text: str) -> list[str]:
    return [t for t in TOKEN_SPLIT.split(text) if t]


def build_dynamic_terms(laws: list[LawTerms], allowed_text: list[str] = ()) -> list[str]:
    """Words of the matched law changes, minus those that also appear in `allowed_text`."""
    allowed = {t for text in allowed_text for t in _tokens(text.lower())}
    words = [
        w
        for law in laws
        for w in [
            *_tokens(law.title),
            *(t for x in law.tax_types for t in _tokens(x)),
            *(t for x in law.norms for t in _tokens(x)),
        ]
    ]
    terms: dict[str, None] = {}
    for word in words:
        lower = word.lower()
        if len(lower) < 4 or lower.isdigit() or lower in STOP_WORDS or lower in allowed:
            continue
        terms[lower] = None
    return list(terms)


@dataclass(frozen=True)
class TermHit:
    term: str
    index: int
    length: int


def find_law_terms(text: str, dynamic_terms: list[str] = ()) -> list[TermHit]:
    """All hits in the text, sorted by position, overlaps removed."""
    patterns = list(FIXED_PATTERNS)
    if dynamic_terms:
        alternatives = "|".join(re.escape(t) for t in dynamic_terms)
        patterns.append(re.compile(rf"(?<!{L})(?:{alternatives})(?!{L})", re.IGNORECASE))
    hits = [
        TermHit(m.group(0), m.start(), len(m.group(0)))
        for pattern in patterns
        for m in pattern.finditer(text)
        if m.group(0).strip()
    ]
    hits.sort(key=lambda h: (h.index, -h.length))
    merged: list[TermHit] = []
    for hit in hits:
        if merged and hit.index < merged[-1].index + merged[-1].length:
            continue
        merged.append(hit)
    return merged


def unique_terms(hits: list[TermHit]) -> list[str]:
    seen: dict[str, str] = {}
    for hit in hits:
        seen.setdefault(hit.term.lower(), hit.term)
    return list(seen.values())


def check_email(subject: str, body: str, terms: list[str]) -> list[TermHit]:
    return find_law_terms(f"{subject}\n{body}", terms)


def terms_for_client(
    company_name: str,
    contact: Contact,
    signer: Signer,
    matched_laws: list[LawTerms],
    template: tuple[str, str] = (DEFAULT_SUBJECT, DEFAULT_BODY),
) -> list[str]:
    """Dynamic terms of one client: words of its matched law changes, minus the client
    name, the contact, the signature and the filled template."""
    subject, body = (fill(t, contact, signer) for t in template)
    allowed = [company_name, contact.name, build_signature(signer), subject, body]
    return build_dynamic_terms(matched_laws, allowed)


def template_problems(subject: str, body: str, all_laws: list[LawTerms]) -> list[str]:
    """German messages that block saving a template (empty means it can be saved). The
    template applies to every client: checked against the words of every law change."""
    messages = []
    if not subject.strip():
        messages.append("Der Betreff darf nicht leer sein.")
    if not body.strip():
        messages.append("Der Text darf nicht leer sein.")
    if PLACEHOLDER_GREETING not in body:
        messages.append(f"Der Text muss den Platzhalter {PLACEHOLDER_GREETING} enthalten.")
    if PLACEHOLDER_SIGNATURE not in body:
        messages.append(f"Der Text muss den Platzhalter {PLACEHOLDER_SIGNATURE} enthalten.")
    unknown = unknown_placeholders(f"{subject}\n{body}")
    if unknown:
        messages.append(f"Unbekannte Platzhalter: {', '.join(unknown)}")

    def strip(text: str) -> str:
        for placeholder in PLACEHOLDERS:
            text = text.replace(placeholder, " " * len(placeholder))
        return text

    terms = build_dynamic_terms(all_laws, [DEFAULT_SUBJECT, DEFAULT_BODY])
    hits = check_email(strip(subject), strip(body), terms)
    if hits:
        found = ", ".join(unique_terms(hits))
        messages.append(f"Die Vorlage enthält gesetzesbezogene Begriffe: {found}")
    return messages
