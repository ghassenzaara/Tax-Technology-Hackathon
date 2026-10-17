"""Step 9, plain code: which profile objects become features and their filter metadata.

Embedded: Merkmale with value `ja`, findings (risk register) and appeals (rechtsbehelf).
Never embedded: `nein` Merkmale (schema rule) and `unbekannt` Merkmale (an unknown fact
must not produce law matches), all other list tables and the company master data; the
rule filter handles those at matching time.
TODO: confirm with the team that `unbekannt` Merkmale stay unembedded (README excludes only nein).
TODO: industry (WZ) has no allowed objekt_typ in `profil.embedding`; extend the constraint
with a company-level type or leave industry to the rule filter (schema owner).
"""

import re

from app.config import Settings
from app.models.features import (
    AppealRow,
    AssessmentRow,
    Feature,
    FindingRow,
    MerkmalRow,
    NormRef,
    ProfileSnapshot,
)

_DIGIT_WORD = re.compile(r"\S*\d\S*")  # any word that contains a digit
_LEFTOVER = re.compile(r"[§%≥≤()\[\]]|\s[-–/,;:]\s")
_SPACES = re.compile(r"\s+")
# Generic words of company names that may appear in a sentence (not identifying).
# TODO: extend once real names show false positives.
_GENERIC_NAME_WORDS = frozenset(
    {
        "gmbh",
        "mbh",
        "ag",
        "kg",
        "se",
        "ug",
        "co",
        "ohg",
        "gbr",
        "b.v.",
        "bv",
        "ltd",
        "inc",
        "haftungsbeschränkt",
        "holding",
        "logistik",
        "logistics",
        "immobilien",
        "verwaltung",
        "verwaltungs",
        "beteiligung",
        "beteiligungs",
        "gesellschaft",
        "group",
        "gruppe",
        "bank",
        "capital",
        "partners",
        "invest",
        "services",
        "service",
        "international",
        "deutschland",
        "germany",
        "herr",
        "frau",
        "kanzlei",
        "partnerschaft",
        "steuerberatungsgesellschaft",
        "wirtschaftsprüfungsgesellschaft",
        "rechtsanwälte",
        "sparkasse",
        "volksbank",
    }
)
_FILE_SUFFIXES = (".pdf", ".xlsx", ".xlsm", ".xls")
_FINDING_KIND = {"risiko": "Steuerliches Risiko", "chance": "Steuerliche Chance"}
_APPEAL_KIND = {"einspruch": "einen Einspruch", "klage": "eine Klage"}
_APPEAL_STATUS = {
    "ruht": "das Verfahren ruht im Hinblick auf ein Musterverfahren",
    "anhaengig": "das Verfahren ist anhängig",
    "erledigt": "das Verfahren ist erledigt",
}


def select_features(snapshot: ProfileSnapshot, settings: Settings) -> list[Feature]:
    """Every expected feature of the company, in a stable order."""
    forbidden = forbidden_terms(snapshot.names)
    features = [
        _merkmal(m, snapshot, settings, forbidden) for m in snapshot.merkmale if m.wert == "ja"
    ]
    features += [_finding(f, snapshot, forbidden) for f in snapshot.findings]
    features += [_appeal(a, snapshot, forbidden) for a in snapshot.appeals]
    return features


def forbidden_terms(names: list[str]) -> list[str]:
    """Full names plus their identifying words (4+ letters, not a generic word). File names
    count only as a whole (their words are ordinary terms such as 'Einspruch')."""
    terms = set()
    for name in names:
        name = name.strip()
        if not name:
            continue
        terms.add(name)
        if name.lower().endswith(_FILE_SUFFIXES):
            terms.add(name.rsplit(".", 1)[0])
            continue
        for word in re.split(r"[\s_\-/,()&]+", name):
            word = word.strip(".")
            if len(word) >= 4 and word.lower() not in _GENERIC_NAME_WORDS and not word.isdigit():
                terms.add(word)
    return sorted(terms, key=lambda t: (-len(t), t))


def term_pattern(term: str) -> re.Pattern:
    """Case-insensitive whole-word match of a name (also for names ending in '.')."""
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)


def clean_text(text: str | None, forbidden: list[str]) -> str:
    """Drop words with digits, forbidden names and stray symbols (for fallback sentences)."""
    text = text or ""
    for term in forbidden:
        text = term_pattern(term).sub(" ", text)
    text = _DIGIT_WORD.sub(" ", text)
    text = _LEFTOVER.sub(" ", text)
    return _SPACES.sub(" ", text).strip(" ,;:-–/.")


def year_open(a: AssessmentRow) -> bool:
    """Same rule as the schema view v_offene_jahre."""
    return not a.verjaehrt and (
        a.vorbehalt_164 == "ja"
        or a.vorlaeufig_165
        or a.ablaufhemmung is not None
        or a.stand != "veranlagt"
    )


def offen_for(
    jahre: tuple[int | None, int | None] | None,
    steuerarten: list[str],
    assessments: list[AssessmentRow],
) -> bool | None:
    """Can a year of the range still be changed? None without a year range.

    Open when a matching assessment is open (v_offene_jahre), or a year has no assessment
    yet and lies after the latest assessed year (not assessed means changeable), or the
    range has no end (it reaches into the future).
    TODO: validate this derivation with the teammate who wrote v_offene_jahre.
    """
    if jahre is None or jahre[0] is None:
        return None
    von, bis = jahre
    if bis is None:
        return True
    years = range(von, bis + 1)
    rows = [
        a
        for a in assessments
        if a.jahr in years and (not steuerarten or a.steuerart in steuerarten)
    ]
    if any(year_open(a) for a in rows):
        return True
    latest = max((a.jahr for a in assessments), default=None)
    assessed = {a.jahr for a in rows}
    return any(y not in assessed and (latest is None or y > latest) for y in years)


def tax_types(norms: list[NormRef], settings: Settings) -> list[str]:
    """Tax types derived from the statutes of the norms (mapping in config)."""
    found = {t for n in norms for t in settings.statute_tax_types.get(n.gesetz, [])}
    return sorted(found)


def _norm_text(norms: list[NormRef]) -> str:
    return "; ".join(f"{n.code} ({n.bezeichnung})" if n.bezeichnung else n.code for n in norms)


def _merkmal(
    m: MerkmalRow, snapshot: ProfileSnapshot, settings: Settings, forbidden: list[str]
) -> Feature:
    facts = {
        "Art": "Merkmal der Gesellschaft (trifft zu)",
        "Bezeichnung": m.bezeichnung or "",
        "Kurztext": m.kurztext or "",
        "Normen": _norm_text(m.normen),
    }
    topic = clean_text(m.bezeichnung, forbidden)
    titles = clean_text(", ".join(n.bezeichnung for n in m.normen if n.bezeichnung), forbidden)
    fallback = (
        f"Die Gesellschaft weist ein steuerlich relevantes Merkmal auf: {topic}"
        if topic
        else "Die Gesellschaft weist ein steuerlich relevantes Merkmal auf"
    )
    fallback += f", betroffen sind {titles}." if titles else "."
    return Feature(
        objekt_typ="merkmal_wert",
        objekt_id=m.id,
        gesellschaft_id=snapshot.gesellschaft_id,
        facts={k: v for k, v in facts.items() if v},
        fallback=fallback,
        forbidden=forbidden,
        steuerarten=tax_types(m.normen, settings),
        normen=[n.code for n in m.normen],
    )


def _finding(f: FindingRow, snapshot: ProfileSnapshot, forbidden: list[str]) -> Feature:
    jahre = (f.jahr_von, f.jahr_bis) if f.jahr_von is not None or f.jahr_bis is not None else None
    facts = {
        "Art": "Feststellung der Tax Due Diligence",
        "Richtung": f.richtung,
        "Titel": f.titel,
        "Sachverhalt": f.kurzsachverhalt or "",
        "Steuerarten": ", ".join(f.steuerarten),
        "Normen": _norm_text(f.normen),
    }
    kind = _FINDING_KIND.get(f.richtung, "Steuerliches Risiko und steuerliche Chance")
    title = clean_text(f.titel, forbidden)
    return Feature(
        objekt_typ="finding",
        objekt_id=f.id,
        gesellschaft_id=snapshot.gesellschaft_id,
        facts={k: v for k, v in facts.items() if v},
        fallback=f"{kind} der Gesellschaft: {title}." if title else f"{kind} der Gesellschaft.",
        forbidden=forbidden,
        steuerarten=sorted(f.steuerarten),
        normen=[n.code for n in f.normen],
        jahre=jahre,
        offen=offen_for(jahre, f.steuerarten, snapshot.assessments),
    )


def _appeal(a: AppealRow, snapshot: ProfileSnapshot, forbidden: list[str]) -> Feature:
    steuerarten = [a.steuerart] if a.steuerart else []
    jahre = (a.jahr, a.jahr) if a.jahr is not None else None
    facts = {
        "Art": f"Rechtsbehelf ({a.art})",
        "Status": a.status,
        "Streitpunkt": a.streitpunkt or "",
        "Rechtsgrundlage des Ruhens": a.ruht_rechtsgrundlage or "",
        "Steuerart": a.steuerart or "",
    }
    kind = _APPEAL_KIND.get(a.art, "einen Rechtsbehelf")
    status = _APPEAL_STATUS.get(a.status, "das Verfahren ist offen")
    # A pending appeal keeps the contested assessment open (Ablaufhemmung).
    offen = a.status != "erledigt" or bool(offen_for(jahre, steuerarten, snapshot.assessments))
    return Feature(
        objekt_typ="rechtsbehelf",
        objekt_id=a.id,
        gesellschaft_id=snapshot.gesellschaft_id,
        facts={k: v for k, v in facts.items() if v},
        fallback=f"Die Gesellschaft hat {kind} gegen einen Steuerbescheid eingelegt, {status}.",
        forbidden=forbidden,
        steuerarten=steuerarten,
        normen=[],  # TODO: map the dispute to a norm when the schema links one
        jahre=jahre,
        offen=offen,
    )
