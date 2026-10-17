"""Step 14, stage 2 (pure code): score(company, section) = max over features of cosine.

Max, never average: one strongly related feature is enough. Plain Python arithmetic is fine
at this size (tens of features times tens of sections).
TODO: switch to pgvector (vector(1536), HNSW) when the data grows.
"""

import math
from collections import defaultdict

from app.config import Settings
from app.matching.rules import Pair
from app.models.law import Law
from app.models.match import ScoredFeature, SectionScore


class MatchError(ValueError):
    """Vectors that must not be compared (different model or dimension)."""


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise MatchError(f"vector dimensions differ ({len(a)} and {len(b)})")
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


def rank_sections(
    pairs: list[Pair], laws: dict[int, Law], form_unknown: set[int], settings: Settings
) -> list[SectionScore]:
    """Every section with its best features, highest score first; ties go to a norm match.
    `passed` marks those above the threshold. The candidate cap (`match_max_candidates`)
    limits the sections judged per screening execution (services/screen.py), not this set:
    a section with a reusable verdict costs no budget."""
    by_section: dict[int, list[Pair]] = defaultdict(list)
    for pair in pairs:
        by_section[pair.section.id].append(pair)

    ranked = []
    for section_pairs in by_section.values():
        section = section_pairs[0].section
        scored = sorted(
            (
                ScoredFeature(
                    feature=p.feature,
                    score=cosine(p.feature.vektor, section.vector),
                    norm_match=p.norm_match,
                )
                for p in section_pairs
            ),
            key=lambda s: (s.score, s.norm_match),
            reverse=True,
        )
        ranked.append(
            SectionScore(
                law=laws[section.law_id],
                section=section,
                features=scored[: settings.judge_features_per_section],
                form_unknown=section.law_id in form_unknown,
            )
        )
    ranked.sort(key=lambda s: (s.score, s.norm_match, -s.section.nr), reverse=True)
    for s in ranked:
        s.passed = s.score >= settings.match_score_threshold
    return ranked
