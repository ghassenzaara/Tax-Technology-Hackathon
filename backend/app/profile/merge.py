"""Step 7: deterministic merge of candidate rows into a profile draft. Plain code, no LLM.

Same value from at least two distinct documents = confirmed; one document = single_source;
different values = conflict, with every candidate and its source kept for the advisor.
Agreement is counted per document, never per row, so chunk overlap or a value repeated in
one document never confirms itself. Output order depends only on the input set.

Numbers are equal only when identical, except monetary amounts (unit EUR), which use the
rounding rule in `values.py`.

List fields (rows such as `veranlagung`) are merged per item: items are matched by the
field's key (for example year + tax type, always compared exactly), then each key is merged
like a scalar field.
Two items with the same key agree when every column both of them state is equal; gaps in
one item are filled from the others. The suggestion for a conflict prefers the strongest
source class (`dokument_klasse.rang`: Bescheid 1 ... Entwurf 9), then more documents.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from app.models.extraction import Candidate, DocumentExtraction, ExtractionStatus
from app.models.profile import (
    CandidateGroup,
    FieldResult,
    FieldStatus,
    IncompleteDocument,
    ProfileDraft,
)
from app.profile.schema import NUMERIC_TYPES, FieldSpec, ProfileSchema
from app.profile.values import (
    differs_by_rounding,
    is_valid,
    precision,
    sort_key,
    values_equal,
)

INCOMPLETE_STATUSES = frozenset(
    {ExtractionStatus.FAILED, ExtractionStatus.SKIPPED, ExtractionStatus.WRITE_FAILED}
)
WEAKEST_RANK = 9  # Entwurf: never evidence on its own


def document_ranks(
    extractions: Iterable[DocumentExtraction], schema: ProfileSchema
) -> dict[str, int]:
    return {
        e.doc_id: schema.document_classes[e.document_class]
        for e in extractions
        if e.document_class in schema.document_classes
    }


def merge_candidates(
    candidates: Iterable[Candidate],
    schema: ProfileSchema,
    *,
    company_id: str,
    max_granularity: int,
    extractions: Iterable[DocumentExtraction] = (),
    ranks: dict[str, int] | None = None,
) -> ProfileDraft:
    extractions = list(extractions)
    ranks = document_ranks(extractions, schema) if ranks is None else ranks
    by_field: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_field[candidate.field_name].append(candidate)  # unknown field names are ignored

    merger = _Merger(max_granularity, ranks)
    fields = [merger.field(spec, by_field[spec.name]) for spec in schema.fields]
    incomplete = sorted(
        (
            IncompleteDocument(
                doc_id=e.doc_id,
                filename=e.filename,
                status=e.status,
                reason="; ".join(e.warnings) or str(e.status),
            )
            for e in extractions
            if e.status in INCOMPLETE_STATUSES
        ),
        key=lambda d: (d.filename, d.doc_id),
    )
    return ProfileDraft(
        company_id=company_id,
        fields=fields,
        counts=dict(Counter(f.status for f in fields)),
        incomplete_documents=incomplete,
        db_incomplete=any(e.status is ExtractionStatus.WRITE_FAILED for e in extractions),
        schema_fields=schema.names,
    )


class _Merger:
    def __init__(self, max_granularity: int, ranks: dict[str, int]):
        self.max_granularity = max_granularity
        self.ranks = ranks

    def field(self, spec: FieldSpec, candidates: list[Candidate]) -> FieldResult:
        """Merge one field. A corrupt stored value is dropped with a warning; an
        unexpected error affects this field only, never the whole draft."""
        valid, warnings = [], []
        for c in candidates:
            if is_valid(spec, c.value):
                valid.append(c)
            else:
                warnings.append(
                    f"dropped candidate from {c.source_filename} p.{c.source_page}: "
                    f"value {c.value!r} does not fit the field type"
                )
        try:
            result = (self.list_field if spec.is_list else self.scalar_field)(spec, valid)
        except Exception as exc:
            result = FieldResult(name=spec.name, status=FieldStatus.MISSING)
            warnings.append(f"merge failed: {type(exc).__name__}: {exc}")
        if warnings:
            result.warnings = warnings
            result.needs_review = True
        return result

    def scalar_field(self, spec: FieldSpec, candidates: list[Candidate]) -> FieldResult:
        gran = self.max_granularity
        return self._merge(
            spec.name,
            candidates,
            same=lambda a, b: values_equal(spec.type, a, b, gran, rounding=spec.rounds),
            value_key=lambda v: sort_key(spec.type, v, gran),
            combine=lambda values: values[0],
            rounded=lambda a, b: differs_by_rounding(spec.type, a, b),
        )

    def list_field(self, spec: FieldSpec, candidates: list[Candidate]) -> FieldResult:
        if not candidates:
            return FieldResult(name=spec.name, status=FieldStatus.MISSING)
        # Group by key: the most complete item first, so the representative is stable.
        keyed: list[list[Candidate]] = []
        for candidate in sorted(candidates, key=lambda c: self._order(c, self._item_key(spec))):
            for group in keyed:
                if self._keys_equal(spec, group[0].value, candidate.value):
                    group.append(candidate)
                    break
            else:
                keyed.append([candidate])

        items = []
        for group in keyed:
            key = {k: group[0].value.get(k) for k in spec.key}
            label = ", ".join(f"{k}={v}" for k, v in key.items())
            result = self._merge(
                f"{spec.name}[{label}]",
                group,
                same=lambda a, b: self._items_equal(spec, a, b),
                value_key=self._item_key(spec),
                combine=_fill_gaps,
                rounded=lambda a, b: self._items_rounded(spec, a, b),
            )
            result.key = key
            items.append(result)

        statuses = {item.status for item in items}
        if FieldStatus.CONFLICT in statuses:
            status = FieldStatus.CONFLICT
        elif statuses == {FieldStatus.CONFIRMED}:
            status = FieldStatus.CONFIRMED
        else:
            status = FieldStatus.SINGLE_SOURCE
        return FieldResult(
            name=spec.name,
            status=status,
            items=items,
            needs_review=any(item.needs_review for item in items),
            rounding_equivalent=any(item.rounding_equivalent for item in items),
            intra_document_conflict=any(item.intra_document_conflict for item in items),
        )

    def _merge(
        self,
        name: str,
        candidates: list[Candidate],
        *,
        same: Callable[[Any, Any], bool],
        value_key: Callable[[Any], tuple],
        combine: Callable[[list[Any]], Any],
        rounded: Callable[[Any, Any], bool],
    ) -> FieldResult:
        if not candidates:
            return FieldResult(name=name, status=FieldStatus.MISSING)

        # Compare against each group's first (most precise) member only: rounding equality
        # is not transitive, so a fixed order and a fixed representative keep it stable.
        members: list[list[Candidate]] = []
        for candidate in sorted(candidates, key=lambda c: self._order(c, value_key)):
            for group in members:
                if same(group[0].value, candidate.value):
                    group.append(candidate)
                    break
            else:
                members.append([candidate])

        groups = [self._group(g, combine, rounded) for g in members]
        if len(groups) > 1:
            docs_per_group = [set(g.documents) for g in groups]
            intra = any(
                a & b for i, a in enumerate(docs_per_group) for b in docs_per_group[i + 1 :]
            )
            suggestion = max(
                range(len(groups)),
                key=lambda i: (
                    -(groups[i].best_rank or WEAKEST_RANK + 1),
                    len(groups[i].documents),
                    groups[i].max_confidence,
                    -i,
                ),
            )
            return FieldResult(
                name=name,
                status=FieldStatus.CONFLICT,
                groups=groups,
                suggestion=suggestion,
                needs_review=self._needs_review(groups[suggestion]),
                intra_document_conflict=intra,
                rounding_equivalent=any(g.rounding_equivalent for g in groups),
            )

        group = groups[0]
        confirmed = len(group.documents) >= 2
        return FieldResult(
            name=name,
            status=FieldStatus.CONFIRMED if confirmed else FieldStatus.SINGLE_SOURCE,
            value=group.value,
            groups=groups,
            rounding_equivalent=group.rounding_equivalent,
            needs_review=self._needs_review(group),
        )

    def _group(
        self,
        members: list[Candidate],
        combine: Callable[[list[Any]], Any],
        rounded: Callable[[Any, Any], bool],
    ) -> CandidateGroup:
        ranks = [self.ranks[c.source_doc] for c in members if c.source_doc in self.ranks]
        return CandidateGroup(
            value=combine([c.value for c in members]),
            documents=sorted({c.source_doc for c in members}),
            candidates=members,
            rounding_equivalent=any(rounded(members[0].value, c.value) for c in members),
            max_confidence=max(c.confidence for c in members),
            best_rank=min(ranks) if ranks else None,
        )

    @staticmethod
    def _needs_review(group: CandidateGroup) -> bool:
        weak = group.best_rank is not None and group.best_rank >= WEAKEST_RANK
        return weak or all(c.needs_review for c in group.candidates)

    def _order(self, c: Candidate, value_key: Callable[[Any], tuple]) -> tuple:
        return (
            value_key(c.value),
            self.ranks.get(c.source_doc, WEAKEST_RANK + 1),
            c.source_doc,
            c.source_page,
            c.chunk_index,
            -c.confidence,
            c.evidence,
        )

    # List items -------------------------------------------------------------------

    def _item_key(self, spec: FieldSpec) -> Callable[[Any], tuple]:
        numeric = [i for i in spec.items if i.type in NUMERIC_TYPES]

        def key(value: dict) -> tuple:
            filled = sum(value.get(i.name) is not None for i in spec.items)
            precise = tuple(
                precision(i.type, value.get(i.name), self.max_granularity) for i in numeric
            )
            return -filled, precise, sort_key(spec.type, value, self.max_granularity)[1]

        return key

    def _keys_equal(self, spec: FieldSpec, a: dict, b: dict) -> bool:
        return all(self._optional_equal(spec.item(k), a.get(k), b.get(k)) for k in spec.key)

    def _optional_equal(self, item: FieldSpec, a: Any, b: Any) -> bool:
        """Key columns: exact (normalized strings), never the rounding rule."""
        if a is None or b is None:
            return a is None and b is None
        return values_equal(item.type, a, b, self.max_granularity)

    def _items_equal(self, spec: FieldSpec, a: dict, b: dict) -> bool:
        """Equal when every column stated in both is equal; a gap is not a disagreement."""
        return all(
            values_equal(i.type, a[i.name], b[i.name], self.max_granularity, rounding=i.rounds)
            for i in spec.items
            if a.get(i.name) is not None and b.get(i.name) is not None
        )

    @staticmethod
    def _items_rounded(spec: FieldSpec, a: dict, b: dict) -> bool:
        return any(differs_by_rounding(i.type, a.get(i.name), b.get(i.name)) for i in spec.items)


def _fill_gaps(values: list[dict]) -> dict:
    merged = dict(values[0])
    for value in values[1:]:
        for name, v in value.items():
            if merged.get(name) is None and v is not None:
                merged[name] = v
    return merged
