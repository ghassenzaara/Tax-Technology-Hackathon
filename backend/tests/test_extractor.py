import asyncio
import json

import openai
import pytest

from app.agents.extractor import MISSING_KEY_WARNING, Extractor
from app.models.document import DocStatus, Table
from app.models.extraction import ExtractionStatus
from tests.helpers import (
    SENTENCE,
    FakeClient,
    Reply,
    answer,
    api_error,
    entry,
    item,
    make_document,
)

EVIDENCE = "Restaurant in Berlin mit 25 Mitarbeitern"


def make_extractor(schema, settings, client):
    return Extractor(schema, settings=settings, client=client, slots=asyncio.Semaphore(5))


async def extract(schema, settings, client, doc=None):
    return await make_extractor(schema, settings, client).extract(
        doc or make_document(), company_id="c1"
    )


async def test_happy_path(schema, settings):
    client = FakeClient(
        responses=[
            answer(
                employees=entry(25, evidence=EVIDENCE),
                legal_form=entry("GmbH", evidence="Die Muster GmbH"),
            )
        ]
    )

    result = await extract(schema, settings, client)

    assert result.status is ExtractionStatus.OK and not result.repair_used
    by_field = {c.field_name: c for c in result.candidates}
    assert set(by_field) == {"employees", "legal_form"}  # null fields produce no rows
    employees = by_field["employees"]
    assert employees.source_doc == "id-doc.pdf" and employees.source_filename == "doc.pdf"
    assert employees.source_page == 1 and employees.evidence == EVIDENCE
    assert employees.confidence == settings.confidence_high and not employees.needs_review
    assert result.token_usage.input_tokens == 100 and result.chunks == 1

    request = client.completions.requests[0]
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["reasoning_effort"] == settings.extraction_reasoning_effort
    assert "temperature" not in request
    assert request["messages"][1]["content"] == make_document().to_markdown()


async def test_same_system_prompt_for_every_document(schema, settings):
    client = FakeClient(text=answer())
    extractor = make_extractor(schema, settings, client)

    await extractor.extract(make_document("a.pdf"), "c1")
    await extractor.extract(make_document("b.pdf", ["Andere Firma AG"] * 2), "c1")

    first, second = (r["messages"][0]["content"] for r in client.completions.requests)
    assert first == second


async def test_repair_then_valid(schema, settings):
    client = FakeClient(
        responses=["not json at all", answer(employees=entry(25, evidence=EVIDENCE))]
    )

    result = await extract(schema, settings, client)

    assert client.completions.calls == 2 and result.repair_used
    assert result.status is ExtractionStatus.OK and len(result.candidates) == 1
    repair_messages = client.completions.requests[1]["messages"]
    assert repair_messages[2] == {"role": "assistant", "content": "not json at all"}
    assert "not valid JSON" in repair_messages[3]["content"]
    assert result.token_usage.input_tokens == 200


async def test_repair_feedback_names_validation_errors(schema, settings):
    client = FakeClient(responses=[answer(legal_form=entry("Limited")), answer()])

    await extract(schema, settings, client)

    feedback = client.completions.requests[1]["messages"][3]["content"]
    assert "legal_form" in feedback


async def test_invalid_twice_fails_without_candidates(schema, settings):
    client = FakeClient(responses=["{}", json.dumps({"employees": "x"})])

    result = await extract(schema, settings, client)

    assert client.completions.calls == 2
    assert result.status is ExtractionStatus.FAILED and result.candidates == []
    assert "extraction failed" in result.warnings[0]


async def test_nonexistent_page_repaired(schema, settings):
    client = FakeClient(
        responses=[
            answer(employees=entry(25, page=7, evidence=EVIDENCE)),
            answer(employees=entry(25, page=1, evidence=EVIDENCE)),
        ]
    )

    result = await extract(schema, settings, client)

    assert "page 7 does not exist" in client.completions.requests[1]["messages"][3]["content"]
    assert result.status is ExtractionStatus.OK and result.candidates[0].source_page == 1


async def test_nonexistent_page_dropped_if_still_wrong(schema, settings):
    wrong = answer(
        employees=entry(25, page=7, evidence=EVIDENCE),
        legal_form=entry("GmbH", evidence="Muster GmbH"),
    )
    client = FakeClient(responses=[wrong, wrong])

    result = await extract(schema, settings, client)

    assert result.status is ExtractionStatus.PARTIAL
    assert [c.field_name for c in result.candidates] == ["legal_form"]
    assert any("no source" in w for w in result.warnings)


async def test_evidence_on_other_page_corrects_page(schema, settings):
    doc = make_document(pages=["Inhaltsverzeichnis", SENTENCE])
    client = FakeClient(responses=[answer(employees=entry(25, page=1, evidence=EVIDENCE))])

    result = await extract(schema, settings, client, doc)

    assert result.candidates[0].source_page == 2
    assert any("found on page 2" in w for w in result.warnings)


async def test_evidence_not_found_is_capped_and_flagged(schema, settings):
    client = FakeClient(responses=[answer(employees=entry(25, evidence="steht hier nicht"))])

    result = await extract(schema, settings, client)

    candidate = result.candidates[0]
    assert candidate.value == 25 and candidate.needs_review
    assert candidate.confidence == settings.confidence_unverified_cap


async def test_evidence_spanning_table_row_is_verified(schema, settings):
    doc = make_document(pages=["Kennzahlen"])
    doc.pages[0].tables = [Table(rows=[["Kennzahl", "Wert"], ["Umsatzerlöse EUR", "1.234,50"]])]
    client = FakeClient(
        responses=[answer(annual_revenue_eur=entry(1234.5, evidence="Umsatzerlöse EUR 1.234,50"))]
    )

    result = await extract(schema, settings, client, doc)

    candidate = result.candidates[0]
    assert candidate.value == 1234.5 and not candidate.needs_review


async def test_ocr_page_caps_confidence(schema, settings):
    doc = make_document(pages=[SENTENCE], ocr_pages=(1,))
    client = FakeClient(responses=[answer(employees=entry(25, evidence=EVIDENCE))])

    result = await extract(schema, settings, client, doc)

    candidate = result.candidates[0]
    assert candidate.confidence == settings.confidence_ocr_cap and candidate.needs_review


async def test_low_confidence_needs_review(schema, settings):
    client = FakeClient(
        responses=[answer(employees=entry(25, evidence=EVIDENCE, confidence="low"))]
    )

    result = await extract(schema, settings, client)

    assert result.candidates[0].confidence == settings.confidence_low
    assert result.candidates[0].needs_review


async def test_rate_limit_retried(schema, settings):
    client = FakeClient(
        errors=[api_error(openai.RateLimitError, 429)],
        responses=[answer(employees=entry(25, evidence=EVIDENCE))],
    )

    result = await extract(schema, settings, client)

    assert client.completions.calls == 2 and result.status is ExtractionStatus.OK


async def test_non_retryable_error_not_retried(schema, settings):
    client = FakeClient(errors=[api_error(openai.AuthenticationError, 401)])

    result = await extract(schema, settings, client)

    assert client.completions.calls == 1 and result.status is ExtractionStatus.FAILED
    assert "AuthenticationError" in result.warnings[0]


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (Reply(None, refusal="I cannot help"), "model refused"),
        (Reply(""), "empty response"),
        (Reply('{"employees": ', finish_reason="length"), "cut off"),
    ],
)
async def test_edge_responses_go_to_repair_then_fail(schema, settings, reply, message):
    client = FakeClient(responses=[reply, reply])

    result = await extract(schema, settings, client)

    assert client.completions.calls == 2 and result.status is ExtractionStatus.FAILED
    assert message in client.completions.requests[1]["messages"][3]["content"]


@pytest.mark.parametrize(
    ("doc", "reason"),
    [
        (make_document(status=DocStatus.FAILED, warnings=["corrupt"]), "document failed"),
        (make_document(status=DocStatus.UNSUPPORTED), "document unsupported"),
        (make_document(pages=["  x  "]), "extractable characters"),
    ],
)
async def test_skip_without_llm_call(schema, settings, doc, reason):
    client = FakeClient()

    result = await extract(schema, settings, client, doc)

    assert client.completions.calls == 0
    assert result.status is ExtractionStatus.SKIPPED and reason in result.warnings[0]


async def test_missing_key_fails_document(schema, settings):
    result = await extract(schema, settings, None)

    assert result.status is ExtractionStatus.FAILED and result.warnings == [MISSING_KEY_WARNING]


async def test_partial_document_carries_parse_warnings(schema, settings):
    doc = make_document(status=DocStatus.PARTIAL)
    doc.pages[0].warnings = ["OCR failed"]
    client = FakeClient(responses=[answer(employees=entry(25, evidence=EVIDENCE))])

    result = await extract(schema, settings, client, doc)

    assert result.status is ExtractionStatus.PARTIAL and len(result.candidates) == 1
    assert any("OCR failed" in w for w in result.warnings)


async def test_list_items_become_candidates_with_document_meta(schema, settings):
    doc = make_document(pages=["Verbleibender Verlustvortrag zum 31.12.2024 2.412.380 €"])
    client = FakeClient(
        responses=[
            answer(
                dokument={
                    "klasse_code": "bescheid",
                    "titel": "Feststellungsbescheid",
                    "dokument_datum": "2026-02-03",
                },
                steuerattribut=[
                    item(
                        art="kst_verlustvortrag",
                        stichtag="2024-12-31",
                        betrag_eur=2412380,
                        evidence="Verlustvortrag zum 31.12.2024 2.412.380 €",
                    ),
                    item(evidence="leer"),  # all columns null: dropped
                ],
            )
        ]
    )

    result = await extract(schema, settings, client, doc)

    (candidate,) = result.candidates
    assert candidate.field_name == "steuerattribut"
    assert candidate.value == {
        "art": "kst_verlustvortrag",
        "stichtag": "2024-12-31",
        "betrag_eur": 2412380,
    }
    assert not candidate.needs_review  # evidence verified on the page
    assert (result.document_class, result.document_title, result.document_date) == (
        "bescheid",
        "Feststellungsbescheid",
        "2026-02-03",
    )
    assert result.status is ExtractionStatus.PARTIAL  # the empty item was dropped
    assert any("steuerattribut[1]: dropped" in w for w in result.warnings)


async def test_list_item_with_wrong_page_goes_to_repair(schema, settings):
    bad = answer(steuerattribut=[item(page=9, art="kst_verlustvortrag")])
    good = answer(steuerattribut=[item(page=1, art="kst_verlustvortrag")])
    client = FakeClient(responses=[bad, good])

    result = await extract(schema, settings, client)

    feedback = client.completions.requests[1]["messages"][3]["content"]
    assert "steuerattribut[0].source_page: page 9 does not exist" in feedback
    assert result.candidates[0].source_page == 1


def chunked(settings):
    values = {"chars_per_token": 1.0, "chunk_trigger_tokens": 300, "chunk_target_tokens": 200}
    return settings.model_copy(update=values | {"chunk_overlap_pages": 0})


async def test_chunks_use_original_pages(schema, settings):
    doc = make_document(pages=[SENTENCE + " " + "x" * 80 for _ in range(4)])

    def responder(kwargs):
        markdown = kwargs["messages"][1]["content"]
        page = int(markdown.split("## Page ")[1].split("\n")[0])  # first page of the chunk
        return answer(employees=entry(25, page=page, evidence=EVIDENCE))

    result = await extract(schema, chunked(settings), FakeClient(responder=responder), doc)

    assert result.chunks == 4 and result.status is ExtractionStatus.OK
    assert sorted(c.source_page for c in result.candidates) == [1, 2, 3, 4]
    assert sorted(c.chunk_index for c in result.candidates) == [0, 1, 2, 3]


async def test_one_failed_chunk_makes_document_partial(schema, settings):
    doc = make_document(pages=[SENTENCE + " " + "x" * 80 for _ in range(2)])

    def responder(kwargs):
        markdown = kwargs["messages"][1]["content"]
        if "## Page 2" in markdown:
            return ValueError("chunk exploded")
        return answer(employees=entry(25, evidence=EVIDENCE))

    result = await extract(schema, chunked(settings), FakeClient(responder=responder), doc)

    assert result.status is ExtractionStatus.PARTIAL and len(result.candidates) == 1
    assert any("chunk 1" in w and "chunk exploded" in w for w in result.warnings)
