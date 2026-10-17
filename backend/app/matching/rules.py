"""Step 13, stage 1 (pure code, no LLM): legal form per company, tax type per pair.

Norm overlap is no filter, only a signal (`norm_match`). Region, industry, time and numeric
thresholds are not filtered in this milestone; the judge sees years and the law status.
TODO: a temporal rule (law effective date against open years) and numeric condition rules.
TODO: decide whether a norm match should bypass the score threshold.
"""

import re
from dataclasses import dataclass, field

from app.models.features import EmbeddingRecord
from app.models.law import Law, LawSection
from app.models.match import CompanyFeatures

# Legal-form groups a law may name instead of single forms (normalised spelling).
# TODO: legal-form aliases (existing TODO); extend after the first real laws.
LEGAL_FORM_GROUPS = {
    "kapitalgesellschaft": {"gmbh", "ag", "ug", "ughaftungsbeschraenkt", "se", "kgaa"},
    "personengesellschaft": {"kg", "ohg", "gbr", "gmbhcokg", "ugcokg", "agcokg", "partg"},
}
LEGAL_FORM_GROUPS["koerperschaft"] = LEGAL_FORM_GROUPS["kapitalgesellschaft"] | {
    "ev",
    "eg",
    "stiftung",
}
_ALIASES = {
    "gesellschaftmitbeschraenkterhaftung": "gmbh",
    "aktiengesellschaft": "ag",
    "kommanditgesellschaft": "kg",
    "offenehandelsgesellschaft": "ohg",
    "kapitalgesellschaften": "kapitalgesellschaft",
    "personengesellschaften": "personengesellschaft",
    "koerperschaften": "koerperschaft",
}
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


@dataclass
class Pair:
    feature: EmbeddingRecord
    section: LawSection
    norm_match: bool = False


@dataclass
class RuleResult:
    pairs: list[Pair] = field(default_factory=list)
    removed: dict[str, int] = field(default_factory=lambda: {"legal_form": 0, "tax_type": 0})
    form_unknown: set[int] = field(default_factory=set)  # law ids passed without legal form


def normalize_form(text: str) -> str:
    """'G.m.b.H.' and 'GmbH' -> 'gmbh'; 'GmbH & Co. KG' -> 'gmbhcokg'."""
    key = re.sub(r"[^a-z0-9]", "", text.casefold().translate(_UMLAUTS))
    return _ALIASES.get(key, key)


def form_allowed(rechtsform: str, legal_forms: list[str]) -> bool:
    form = normalize_form(rechtsform)
    allowed = {normalize_form(f) for f in legal_forms}
    return form in allowed or any(form in LEGAL_FORM_GROUPS.get(f, ()) for f in allowed)


def filter_pairs(
    company: CompanyFeatures, laws: dict[int, Law], sections: list[LawSection]
) -> RuleResult:
    """Surviving (feature, section) pairs of one model, with removal counts per rule."""
    result = RuleResult()
    for section in sections:
        law = laws[section.law_id]
        features = [f for f in company.features if f.modell == section.model]
        if law.legal_forms:
            if company.rechtsform is None:  # missing data never drops a law
                result.form_unknown.add(law.id)
            elif not form_allowed(company.rechtsform, law.legal_forms):
                result.removed["legal_form"] += len(features)
                continue
        for feature in features:
            if not _tax_types_overlap(feature.steuerarten, section.tax_types):
                result.removed["tax_type"] += 1
                continue
            norm_match = bool(set(feature.normen) & set(section.norms))
            result.pairs.append(Pair(feature, section, norm_match))
    return result


def _tax_types_overlap(feature: list[str], section: list[str]) -> bool:
    """Unknown (empty) on either side must not hide a hit."""
    return not feature or not section or bool(set(feature) & set(section))
