"""Steps 11 and 12: splitter, norm regex, metadata call and law ingest. FakeClient only."""

import json

import pytest

from app.db.memory import MemoryRepository
from app.db.repository import DuplicateLawError
from app.ingest.pipeline import DocumentParser
from app.laws.metadata import read_metadata
from app.laws.split import find_norms, heading_ref, section_tax_types, split_law
from app.llm import InvalidOutput
from app.models.law import LawKind, LawStatus
from app.services.law_ingest import IngestError, ingest_law
from tests.helpers import FakeClient, Reply, make_document, make_pdf

VOCABULARY = {"§ 8c KStG", "§ 10a GewStG", "§ 9 Nr. 1 S. 2 GewStG", "§ 1 Abs. 2b GrEStG"}
FILLER = "Die Regelung gilt für alle Körperschaften mit Verlustvorträgen im Inland. "

METADATA = {
    "kind": "gesetzentwurf",
    "title": "Entwurf eines Gesetzes zur Modernisierung des Verlustabzugs",
    "reference": "Drucksache 21/123",
    "published_at": "2025-03-01",
    "effective_at": "2026-01-01",
    "status": "entwurf",
    "legal_forms": [],
    "tax_types": ["KSt", "GewSt"],
    "summary": "Der Entwurf ändert den Verlustabzug. Er betrifft Anteilsübertragungen.",
    "change_summary": "Der Entwurf ändert die Regeln zum Verlustabzug bei Anteilsübertragungen.",
}


@pytest.fixture
def small(settings):
    """Small section sizes, so short test texts exercise merging and splitting."""
    return settings.model_copy(
        update={"section_min_tokens": 10, "section_target_tokens": 40, "section_max_tokens": 60}
    )


def law_pdf(tmp_path, name="gesetz.pdf"):
    return make_pdf(
        tmp_path / name,
        [
            "§ 8c Verlustabzug bei Körperschaften\n"
            "Werden innerhalb von fünf Jahren mehr als 50 Prozent der Anteile übertragen,\n"
            "gehen nicht genutzte Verluste nach § 8c Abs. 1 Satz 1 KStG unter.",
            "§ 10a Gewerbeverlust\n"
            "Der Gewerbeverlust nach § 10a GewStG folgt den Regeln des Verlustabzugs\n"
            "und geht bei einem schädlichen Beteiligungserwerb ebenfalls unter.",
        ],
    )


def test_cut_at_headings_with_pages_norms_and_tax_types(small):
    doc = make_document(
        pages=[
            "§ 8c Verlustabzug bei Körperschaften\nNach § 8c Abs. 1 Satz 1 KStG " + FILLER,
            "§ 10a Gewerbeverlust\nDer Verlust nach § 10a GewStG " + FILLER,
        ]
    )

    result = split_law(doc, small, VOCABULARY, ["USt"])

    assert result.strategy == "headings"
    assert [s.section_ref for s in result.sections] == ["§ 8c", "§ 10a"]
    first, second = result.sections
    assert (first.page_from, first.page_to, second.page_from) == (1, 1, 2)
    assert first.heading == "§ 8c Verlustabzug bei Körperschaften"
    assert (first.norms, first.tax_types) == (["§ 8c KStG"], ["KSt"])
    assert (second.norms, second.tax_types) == (["§ 10a GewStG"], ["GewSt"])
    assert [s.nr for s in result.sections] == [1, 2]


def test_tiny_pieces_merge_into_the_next(small):
    doc = make_document(pages=["Rz. 1 Kurz.\nRz. 2 Auch kurz.\nRz. 3 " + FILLER])

    (section,) = split_law(doc, small, VOCABULARY, []).sections

    assert section.section_ref == "Rz. 1 bis Rz. 3"
    assert section.text.startswith("Rz. 1 Kurz.") and section.text.endswith("Inland.")


def test_oversized_piece_splits_on_paragraphs(small):
    paragraphs = "\n\n".join(f"Absatz {n}. {FILLER}" for n in range(1, 7))
    doc = make_document(pages=["§ 3 Anwendung\n" + paragraphs])

    sections = split_law(doc, small, VOCABULARY, []).sections

    assert len(sections) > 1
    assert sections[0].section_ref == "§ 3"
    assert {s.section_ref for s in sections[1:]} == {"§ 3 (Fortsetzung)"}
    assert all(s.heading == "§ 3 Anwendung" for s in sections)
    assert sections[1].embedding_text().startswith("§ 3 Anwendung\n")
    assert all(len(s.text) / small.chars_per_token <= small.section_max_tokens for s in sections)


def test_no_headings_falls_back_to_pages(small):
    doc = make_document(pages=["erste seite " + FILLER, "zweite seite " + FILLER])

    result = split_law(doc, small, VOCABULARY, ["KSt"])

    assert result.strategy == "pages"
    assert [s.section_ref for s in result.sections] == ["Seite 1", "Seite 2"]
    assert result.sections[0].tax_types == ["KSt"]  # no norm: the law's tax types


def test_boilerplate_is_dropped_and_reported(small):
    doc = make_document(
        pages=[
            "Inhaltsverzeichnis\n§ 8c Verlustabzug ........ 2",
            "§ 8c Verlustabzug\n" + FILLER + "\n2",
            "Im Auftrag\nMuster",
        ]
    )

    result = split_law(doc, small, VOCABULARY, [])

    assert [s.section_ref for s in result.sections] == ["§ 8c"]
    assert "table of contents (page 1)" in result.dropped
    assert "signature block (page 3)" in result.dropped
    assert "page numbers (1 lines)" in result.dropped
    assert not result.sections[0].text.endswith("2")


def test_norm_regex_normalises_and_reports_unknown():
    text = (
        "nach § 8c Abs. 1 Satz 1 KStG, § 9 Nr. 1 Satz 2 GewStG, § 1 Absatz 2b GrEStG "
        "und § 99 XyzG; nicht: § 8c (ohne Gesetz)"
    )

    found, unmatched = find_norms(text, VOCABULARY)

    assert found == ["§ 8c KStG", "§ 9 Nr. 1 S. 2 GewStG", "§ 1 Abs. 2b GrEStG"]
    assert unmatched == ["§ 99 XyzG"]


def test_heading_rules():
    assert heading_ref("§ 8c Verlustabzug bei Körperschaften") == "§ 8c"
    assert heading_ref("§ 8c KStG ist erstmals anzuwenden") is None  # running text
    assert heading_ref("Artikel 3 Inkrafttreten") == "Artikel 3"
    assert heading_ref("2.3 Anwendungsbereich") == "Nr. 2.3"
    assert heading_ref("Rz. 12 Die Verwaltung") == "Rz. 12"
    assert heading_ref("die Körperschaft") is None


def test_bfh_margin_numbers_and_citations(small):
    doc = make_document(
        pages=[
            "ECLI:DE:BFH:2025:U.051125.IR37.22.0\nLeitsätze\n1. Der Leitsatz.\n"
            "Tatbestand\nI.\n1 Die Beteiligten streiten. " + FILLER,
            "2 a) Die Klägerin erklärt. " + FILLER + "\nEntscheidungsgründe\nII.\n"
            "3 1. Die Revision ist unbegründet. " + FILLER + "\n"
            "75 Jahre Hochschule für Finanzen, 2025, 191.\n4 b) Der Senat hält daran fest. "
            + FILLER,
        ]
    )
    # Keep paragraphs separate so refs and pages can be asserted independently.
    config = small.model_copy(update={"section_min_tokens": 1, "section_max_tokens": 800})
    result = split_law(doc, config, set(), ["KSt"])
    margins = [s for s in result.sections if s.section_ref.startswith("Rn.")]
    assert [(s.section_ref, s.page_from) for s in margins] == [
        ("Rn. 1", 1),
        ("Rn. 2", 2),
        ("Rn. 3", 2),
        ("Rn. 4", 2),
    ]
    assert "75 Jahre" in margins[2].text
    assert not any(s.section_ref == "Nr. 75" for s in result.sections)


def test_tax_types_from_statutes(settings):
    assert section_tax_types(["§ 8c KStG", "§ 99 XyzG"], ["USt"], settings) == ["KSt"]
    assert section_tax_types([], ["USt"], settings) == ["USt"]


async def test_generated_pdf_splits_by_page(tmp_path, small):
    doc = await DocumentParser(small, client=None).parse_file(law_pdf(tmp_path))

    result = split_law(doc, small, VOCABULARY, [])

    assert [(s.section_ref, s.page_from) for s in result.sections] == [("§ 8c", 1), ("§ 10a", 2)]
    assert result.sections[0].norms == ["§ 8c KStG"]


async def test_metadata_valid_and_repaired(settings):
    doc = make_document(pages=["Entwurf eines Gesetzes"])
    client = FakeClient(responses=[Reply(None, refusal="nein"), json.dumps(METADATA)])

    metadata, tokens = await read_metadata(doc, settings, client, _slots())

    assert metadata.kind is LawKind.GESETZENTWURF and metadata.status is LawStatus.ENTWURF
    assert str(metadata.effective_at) == "2026-01-01"
    assert metadata.change_summary == METADATA["change_summary"]
    assert client.completions.calls == 2 and tokens.input_tokens == 200
    request = client.completions.requests[0]
    assert request["response_format"]["json_schema"]["strict"] is True


async def test_change_summary_unknown_is_null_and_blank_is_repaired(settings):
    client = FakeClient(
        responses=[
            json.dumps({**METADATA, "change_summary": "  "}),
            json.dumps({**METADATA, "change_summary": None}),
        ]
    )
    metadata, _ = await read_metadata(make_document(), settings, client, _slots())
    assert metadata.change_summary is None and client.completions.calls == 2


@pytest.mark.parametrize(
    "answers",
    [
        ["not json", "still not json"],
        [Reply("{", finish_reason="length"), json.dumps({**METADATA, "kind": "roman"})],
    ],
    ids=["bad-json", "truncated-then-invalid"],
)
async def test_metadata_invalid_after_repair_fails(settings, answers):
    client = FakeClient(responses=answers)

    with pytest.raises(InvalidOutput):
        await read_metadata(make_document(), settings, client, _slots())


async def test_ingest_stores_law_and_refuses_duplicates(tmp_path, small):
    repository = MemoryRepository()
    repository.norm_codes = set(VOCABULARY)
    client = FakeClient(text=json.dumps(METADATA))
    path = law_pdf(tmp_path)

    report = await ingest_law(path, repository, small, client)

    assert report.sections == 2 and report.strategy == "headings"
    assert report.norms_found == ["§ 8c KStG", "§ 10a GewStG"]
    (law,) = await repository.list_laws()
    assert (law.title, law.sections, law.filename) == (METADATA["title"], 2, "gesetz.pdf")
    assert law.change_summary == report.change_summary == METADATA["change_summary"]
    assert report.summary == METADATA["summary"]
    sections = await repository.list_law_sections(law.id)
    assert all(s.model == small.embedding_model and len(s.vector) == 4 for s in sections)

    calls = client.completions.calls
    with pytest.raises(DuplicateLawError):
        await ingest_law(path, repository, small, client)
    assert client.completions.calls == calls  # refused before any LLM call

    replaced = await ingest_law(path, repository, small, client, replace=True)
    assert replaced.replaced and replaced.law_id != report.law_id
    assert [x.id for x in await repository.list_laws()] == [replaced.law_id]
    assert len(await repository.list_law_sections()) == 2

    before = await repository.list_law_sections(replaced.law_id)
    await repository.update_law_change_summary(replaced.law_id, "Das Gericht klärt die Regel.")
    assert (await repository.list_laws())[0].change_summary == "Das Gericht klärt die Regel."
    assert await repository.list_law_sections(replaced.law_id) == before


async def test_failed_embedding_writes_nothing(tmp_path, small):
    repository = MemoryRepository()
    client = FakeClient(text=json.dumps(METADATA), vectors=lambda texts: [[1.0] * 4])

    with pytest.raises(IngestError, match="expected 2 vectors"):
        await ingest_law(law_pdf(tmp_path), repository, small, client)

    assert await repository.list_laws() == [] and await repository.list_law_sections() == []


async def test_invalid_metadata_writes_nothing(tmp_path, small):
    repository = MemoryRepository()

    with pytest.raises(IngestError, match="ingest of gesetz.pdf failed"):
        await ingest_law(law_pdf(tmp_path), repository, small, FakeClient(text="kein json"))

    assert await repository.list_laws() == []


def _slots():
    import asyncio

    return asyncio.Semaphore(2)
