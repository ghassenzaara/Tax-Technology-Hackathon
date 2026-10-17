import itertools

from app.models.extraction import Candidate, DocumentExtraction, ExtractionStatus
from app.models.profile import FieldStatus
from app.profile.merge import merge_candidates


def cand(field, value, doc="d1", page=1, confidence=0.9, chunk=0, needs_review=False):
    return Candidate(
        company_id="c1",
        field_name=field,
        value=value,
        evidence=f"evidence {value}",
        source_doc=doc,
        source_filename=f"{doc}.pdf",
        source_page=page,
        confidence=confidence,
        needs_review=needs_review,
        chunk_index=chunk,
    )


def merge(candidates, schema, extractions=()):
    return merge_candidates(
        candidates, schema, company_id="c1", max_granularity=1000, extractions=extractions
    )


def test_same_value_in_two_documents_is_confirmed(schema):
    draft = merge([cand("employees", 25, "d1"), cand("employees", 25, "d2", page=3)], schema)

    result = draft.field("employees")
    assert result.status is FieldStatus.CONFIRMED and result.value == 25
    assert result.groups[0].documents == ["d1", "d2"]


def test_same_value_twice_in_one_document_is_single_source(schema):
    draft = merge([cand("employees", 25, chunk=0), cand("employees", 25, page=2, chunk=1)], schema)

    assert draft.field("employees").status is FieldStatus.SINGLE_SOURCE


def test_different_values_conflict_with_sources(schema):
    draft = merge(
        [
            cand("annual_revenue_eur", 1_000_000, "d1", page=2),
            cand("annual_revenue_eur", 1_500_000, "d2", page=5),
            cand("annual_revenue_eur", 1_500_000, "d3", page=1),
        ],
        schema,
    )

    result = draft.field("annual_revenue_eur")
    assert result.status is FieldStatus.CONFLICT and result.value is None
    assert [g.value for g in result.groups] == [1_000_000, 1_500_000]  # deterministic order
    sources = {(c.source_doc, c.source_page) for g in result.groups for c in g.candidates}
    assert sources == {("d1", 2), ("d2", 5), ("d3", 1)}
    assert result.groups[result.suggestion].value == 1_500_000  # more documents agree
    assert not result.intra_document_conflict


def test_conflict_inside_one_document_is_flagged(schema):
    draft = merge([cand("employees", 25, chunk=0), cand("employees", 40, page=9, chunk=1)], schema)

    assert draft.field("employees").intra_document_conflict


def test_rounding_equivalent_values_merge_to_most_precise(schema):
    draft = merge(
        [cand("annual_revenue_eur", 1_235_000, "d1"), cand("annual_revenue_eur", 1_234_567, "d2")],
        schema,
    )

    result = draft.field("annual_revenue_eur")
    assert result.status is FieldStatus.CONFIRMED
    assert result.value == 1_234_567 and result.rounding_equivalent


def test_non_transitive_rounding_is_order_independent(schema):
    # 1,234,450 ~ 1,234,000 and 1,234,450 ~ 1,234,500, but 1,234,000 and 1,234,500 differ.
    candidates = [
        cand("annual_revenue_eur", 1_234_000, "d1"),
        cand("annual_revenue_eur", 1_234_500, "d2"),
        cand("annual_revenue_eur", 1_234_450, "d3"),
        cand("annual_revenue_eur", 1_240_000, "d4"),
    ]
    drafts = {
        merge(list(order), schema)
        .field("annual_revenue_eur")
        .model_dump_json(
            exclude={"groups": {"__all__": {"candidates": {"__all__": {"created_at"}}}}}
        )
        for order in itertools.permutations(candidates)
    }
    assert len(drafts) == 1


def test_missing_field(schema):
    draft = merge([cand("employees", 25)], schema)

    assert draft.field("company_name").status is FieldStatus.MISSING
    assert draft.counts[FieldStatus.MISSING] == 4  # three scalars and the list


def test_string_values_compared_after_normalization(schema):
    draft = merge(
        [cand("company_name", "Muster GmbH", "d1"), cand("company_name", "MUSTER GMBH.", "d2")],
        schema,
    )

    result = draft.field("company_name")
    assert result.status is FieldStatus.CONFIRMED and not result.rounding_equivalent


def test_failed_and_skipped_documents_listed_as_incomplete(schema):
    extractions = [
        DocumentExtraction(
            company_id="c1",
            doc_id="x",
            filename="x.pdf",
            status=ExtractionStatus.FAILED,
            warnings=["boom"],
        ),
        DocumentExtraction(
            company_id="c1", doc_id="y", filename="y.docx", status=ExtractionStatus.SKIPPED
        ),
        DocumentExtraction(company_id="c1", doc_id="z", filename="z.pdf", status="ok"),
    ]
    draft = merge([], schema, extractions)

    assert [d.filename for d in draft.incomplete_documents] == ["x.pdf", "y.docx"]
    assert draft.incomplete_documents[0].reason == "boom"
    assert not draft.db_incomplete


def test_needs_review_flag(schema):
    draft = merge([cand("employees", 25, needs_review=True)], schema)

    assert draft.field("employees").needs_review


def attr(art, stichtag, betrag, doc="d1", **kwargs):
    value = {"art": art, "stichtag": stichtag, "betrag_eur": betrag}
    return cand("steuerattribut", value, doc, **kwargs)


def test_years_in_list_keys_never_merge(profil_schema):
    # Review finding 1: veranlagung 2020 and 2024 must stay two rows.
    row = {i.name: None for i in profil_schema.field("veranlagung").items}

    def year(jahr):
        value = row | {"jahr": jahr, "steuerart": "KSt", "bescheidart": "festsetzung"}
        return cand("veranlagung", value, "notice")

    draft = merge_candidates(
        [year(2020), year(2024)], profil_schema, company_id="c1", max_granularity=1000
    )

    assert sorted(i.key["jahr"] for i in draft.field("veranlagung").items) == [2020, 2024]


def test_quota_60_vs_55_is_a_conflict(profil_schema):
    row = {i.name: None for i in profil_schema.field("beteiligung").items}
    holder = row | {"gesellschafter": "Henrik Albers", "gueltig_ab": "2019-08-15"}
    draft = merge_candidates(
        [
            cand("beteiligung", holder | {"quote_prozent": 60}, "d1"),
            cand("beteiligung", holder | {"quote_prozent": 55}, "d2"),
        ],
        profil_schema,
        company_id="c1",
        max_granularity=1000,
    )

    (holding,) = draft.field("beteiligung").items
    assert holding.status is FieldStatus.CONFLICT


def test_corrupt_stored_value_only_affects_its_field(schema):
    # Review finding 8: "n/a" in a numeric field drops that candidate, the draft still builds.
    draft = merge(
        [
            cand("annual_revenue_eur", "n/a", "d1"),
            cand("annual_revenue_eur", 1_000_000, "d2"),
            cand("employees", 25, "d1"),
            cand("steuerattribut", {"art": "kst_verlustvortrag", "betrag_eur": "x"}, "d1"),
        ],
        schema,
    )

    revenue = draft.field("annual_revenue_eur")
    assert revenue.status is FieldStatus.SINGLE_SOURCE and revenue.value == 1_000_000
    assert revenue.needs_review and "does not fit the field type" in revenue.warnings[0]
    assert draft.field("employees").value == 25
    assert draft.field("steuerattribut").status is FieldStatus.MISSING
    assert draft.field("steuerattribut").warnings


def test_conflict_suggestion_prefers_stronger_source(schema):
    # The DD report (rank 3) rounds; the notice (rank 1) is exact. Two reports vs one notice.
    candidates = [
        cand("employees", 40, "report_a"),
        cand("employees", 40, "report_b"),
        cand("employees", 25, "notice"),
    ]
    ranks = {"report_a": 3, "report_b": 3, "notice": 1}

    result = merge_candidates(
        candidates, schema, company_id="c1", max_granularity=1000, ranks=ranks
    ).field("employees")

    assert result.status is FieldStatus.CONFLICT
    assert result.chosen == 25 and result.groups[result.suggestion].best_rank == 1


def test_value_only_from_draft_document_needs_review(schema):
    result = merge_candidates(
        [cand("employees", 25, "entwurf")],
        schema,
        company_id="c1",
        max_granularity=1000,
        ranks={"entwurf": 9},
    ).field("employees")

    assert result.status is FieldStatus.SINGLE_SOURCE and result.needs_review


def test_list_items_merge_by_key(schema):
    draft = merge(
        [
            attr("kst_verlustvortrag", "2024-12-31", 2_412_380, "notice"),
            attr("kst_verlustvortrag", "2024-12-31", 2_412_000, "report"),  # rounding only
            attr("gewst_fehlbetrag", "2024-12-31", 1_790_328, "notice"),
            attr("kst_verlustvortrag", "2023-12-31", 2_637_810, "notice"),
        ],
        schema,
    )

    result = draft.field("steuerattribut")
    assert result.status is FieldStatus.SINGLE_SOURCE  # one key confirmed, two single source
    by_key = {(i.key["art"], i.key["stichtag"]): i for i in result.items}
    assert len(by_key) == 3
    kst = by_key[("kst_verlustvortrag", "2024-12-31")]
    assert kst.status is FieldStatus.CONFIRMED and kst.rounding_equivalent
    assert kst.value["betrag_eur"] == 2_412_380  # most precise member


def test_list_item_conflict_and_gap_filling(schema):
    draft = merge(
        [
            attr("kst_verlustvortrag", "2024-12-31", 2_412_380, "notice"),
            attr("kst_verlustvortrag", "2024-12-31", 2_600_000, "report"),
            attr("gewst_fehlbetrag", "2024-12-31", None, "report"),
            attr("gewst_fehlbetrag", "2024-12-31", 1_790_328, "notice"),
        ],
        schema,
        extractions=[
            DocumentExtraction(
                company_id="c1", doc_id=doc, filename=doc, status="ok", document_class=klasse
            )
            for doc, klasse in (("notice", "bescheid"), ("report", "dd_report"))
        ],
    )

    result = draft.field("steuerattribut")
    by_art = {i.key["art"]: i for i in result.items}
    assert result.status is FieldStatus.CONFLICT
    kst = by_art["kst_verlustvortrag"]
    assert kst.status is FieldStatus.CONFLICT and kst.chosen["betrag_eur"] == 2_412_380
    gewst = by_art["gewst_fehlbetrag"]  # a gap is no disagreement: filled from the other
    assert gewst.status is FieldStatus.CONFIRMED and gewst.value["betrag_eur"] == 1_790_328
