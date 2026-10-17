import asyncio
import time

import pytest

from app.agents.orchestrator import Orchestrator
from app.db.memory import MemoryRepository
from app.models.extraction import ExtractionStatus
from app.models.profile import FieldStatus
from tests.helpers import SENTENCE, FakeClient, answer, entry, make_document

EVIDENCE = "Restaurant in Berlin mit 25 Mitarbeitern"
ANSWER = answer(employees=entry(25, evidence=EVIDENCE))


async def stream(docs, delays=None):
    """Stub for DocumentParser.parse_folder: yields documents, optionally after a delay."""
    for doc in docs:
        if delays and doc.filename in delays:
            await asyncio.sleep(delays[doc.filename])
        yield doc


def orchestrator(schema, settings, client, repository=None, concurrency=5):
    limited = settings.model_copy(update={"agent_concurrency": concurrency})
    return Orchestrator(schema, repository or MemoryRepository(), limited, client)


@pytest.mark.parametrize(
    ("documents", "limit", "low", "high"), [(10, 5, 1.9, 3.0), (20, 20, 0.9, 1.9)]
)
async def test_parallel_under_limit(schema, settings, documents, limit, low, high):
    client = FakeClient(text=ANSWER, delay=1.0)
    docs = [make_document(f"d{i}.pdf") for i in range(documents)]

    start = time.perf_counter()
    run = await orchestrator(schema, settings, client, concurrency=limit).run(stream(docs), "c1")
    elapsed = time.perf_counter() - start

    assert len(run.extractions) == documents
    assert all(e.status is ExtractionStatus.OK for e in run.extractions)
    assert client.completions.max_in_flight == limit
    assert low < elapsed < high, f"got {elapsed:.2f}s"
    assert run.draft.field("employees").status is FieldStatus.CONFIRMED
    assert run.summary.headline() == f"{documents} documents extracted in {elapsed:.1f} seconds"


async def test_agents_start_before_slow_document_arrives(schema, settings):
    finished: dict[str, float] = {}
    start = time.perf_counter()

    def responder(kwargs):
        name = kwargs["messages"][1]["content"].split("\n")[0].removeprefix("# Document: ")
        finished[name] = time.perf_counter() - start
        return ANSWER

    docs = [make_document("fast_1.pdf"), make_document("slow.pdf"), make_document("fast_2.pdf")]
    run = await orchestrator(schema, settings, FakeClient(responder=responder)).run(
        stream(docs, delays={"slow.pdf": 1.0}), "c1"
    )

    assert len(run.extractions) == 3
    assert finished["fast_1.pdf"] < 0.5  # ran while slow.pdf was still being "parsed"
    assert finished["slow.pdf"] >= 1.0


async def test_results_yielded_in_completion_order(schema, settings):
    def responder(kwargs):
        return ANSWER

    client = FakeClient(responder=responder)
    docs = [make_document("a.pdf"), make_document("b.pdf")]
    orch = orchestrator(schema, settings, client)
    slow_extract = orch.extractor.extract

    async def extract(doc, company_id):
        if doc.filename == "a.pdf":
            await asyncio.sleep(0.3)
        return await slow_extract(doc, company_id)

    orch.extractor.extract = extract
    order = [e.filename async for e in orch.extract_stream(stream(docs), "c1")]

    assert order == ["b.pdf", "a.pdf"]


class FlakyRepository(MemoryRepository):
    def __init__(self, broken: str):
        super().__init__()
        self.broken = broken

    async def replace_document_candidates(self, extraction):
        if extraction.filename == self.broken:
            raise ConnectionError("db down")
        await super().replace_document_candidates(extraction)


async def test_failures_are_isolated(schema, settings):
    def responder(kwargs):
        if "# Document: llm_error.pdf" in kwargs["messages"][1]["content"]:
            return ValueError("model exploded")
        return ANSWER

    docs = [
        make_document("good.pdf"),
        make_document("broken.pdf", status="failed", warnings=["corrupt"]),
        make_document("llm_error.pdf"),
        make_document("db_error.pdf"),
    ]
    repository = FlakyRepository(broken="db_error.pdf")
    run = await orchestrator(schema, settings, FakeClient(responder=responder), repository).run(
        stream(docs), "c1"
    )

    status = {e.filename: e.status for e in run.extractions}
    assert status == {
        "good.pdf": ExtractionStatus.OK,
        "broken.pdf": ExtractionStatus.SKIPPED,
        "llm_error.pdf": ExtractionStatus.FAILED,
        "db_error.pdf": ExtractionStatus.WRITE_FAILED,
    }
    assert {c.source_filename for c in await repository.list_candidates("c1")} == {"good.pdf"}
    # The merge still uses the candidates the DB missed, and says the DB is incomplete.
    assert run.draft.field("employees").status is FieldStatus.CONFIRMED
    assert run.draft.db_incomplete
    assert {d.filename for d in run.draft.incomplete_documents} == {
        "broken.pdf",
        "llm_error.pdf",
        "db_error.pdf",
    }
    assert await repository.get_profile_draft("c1") == run.draft


async def test_stream_errors_propagate(schema, settings):
    async def broken():
        yield make_document("a.pdf")
        raise NotADirectoryError("not a folder")

    with pytest.raises(NotADirectoryError):
        await orchestrator(schema, settings, FakeClient(text=ANSWER)).run(broken(), "c1")


async def test_consumer_stopping_early_cancels_pending(schema, settings):
    client = FakeClient(text=ANSWER, delay=5.0)
    docs = [make_document(f"d{i}.pdf") for i in range(3)] + [
        make_document("skip.pdf", status="unsupported")
    ]
    orch = orchestrator(schema, settings, client)

    start = time.perf_counter()
    results = orch.extract_stream(stream(docs), "c1")
    first = await anext(results)
    await results.aclose()
    await asyncio.sleep(0.05)  # let the cancelled tasks unwind

    assert first.filename == "skip.pdf"  # skipped documents take no slot and finish first
    assert time.perf_counter() - start < 1.0
    assert client.completions.in_flight == 0


async def test_rerun_replaces_candidates(schema, settings):
    repository = MemoryRepository()
    client = FakeClient(text=ANSWER)
    docs = [make_document("a.pdf"), make_document("b.pdf")]

    for _ in range(2):
        await orchestrator(schema, settings, client, repository).run(stream(docs), "c1")

    assert len(await repository.list_candidates("c1")) == 2


async def test_run_merge_ignores_documents_no_longer_in_the_folder(schema, settings):
    # Review finding 3: a file removed between runs must not contribute candidates.
    repository = MemoryRepository()
    client = FakeClient(text=ANSWER)
    first = [make_document("a.pdf"), make_document("removed.pdf")]
    await orchestrator(schema, settings, client, repository).run(stream(first), "c1")

    run = await orchestrator(schema, settings, client, repository).run(
        stream([make_document("a.pdf")]), "c1"
    )

    employees = run.draft.field("employees")
    assert employees.status is FieldStatus.SINGLE_SOURCE
    assert employees.groups[0].documents == ["id-a.pdf"]
    merged_only = await orchestrator(schema, settings, client, repository).merge("c1")
    assert merged_only.field("employees").status is FieldStatus.CONFIRMED  # all stored docs


async def test_write_failed_document_uses_only_fresh_candidates(schema, settings):
    # Review finding 3: stale stored rows plus new in-memory rows must not fake a conflict.
    repository = FlakyRepository(broken="nobody")
    old = answer(employees=entry(40, evidence=EVIDENCE))
    await orchestrator(schema, settings, FakeClient(text=old), repository).run(
        stream([make_document("a.pdf")]), "c1"
    )
    repository.broken = "a.pdf"

    run = await orchestrator(schema, settings, FakeClient(text=ANSWER), repository).run(
        stream([make_document("a.pdf")]), "c1"
    )

    assert run.extractions[0].status is ExtractionStatus.WRITE_FAILED
    employees = run.draft.field("employees")
    assert employees.status is FieldStatus.SINGLE_SOURCE and employees.value == 25


async def test_failed_rerun_keeps_rank_and_candidates(schema, settings):
    # Review finding 2: an outage in run 2 keeps run 1's facts in the draft.
    repository = MemoryRepository()
    await orchestrator(schema, settings, FakeClient(text=ANSWER), repository).run(
        stream([make_document("a.pdf")]), "c1"
    )
    outage = FakeClient(responder=lambda kwargs: ValueError("OpenAI down"))

    run = await orchestrator(schema, settings, outage, repository).run(
        stream([make_document("a.pdf")]), "c1"
    )

    assert run.extractions[0].status is ExtractionStatus.FAILED
    employees = run.draft.field("employees")
    assert employees.value == 25 and employees.groups[0].best_rank == 1
    assert any("earlier candidates" in d.reason for d in run.draft.incomplete_documents)


async def test_logs_one_line_per_document(schema, settings, caplog):
    caplog.set_level("INFO", logger="app.agents.orchestrator")
    docs = [make_document("a.pdf"), make_document("b.pdf")]

    await orchestrator(schema, settings, FakeClient(text=ANSWER)).run(stream(docs), "c1")

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("extracted ")]
    assert len(lines) == 2 and all("tokens_in=100" in line for line in lines)
    assert any("2 documents extracted in" in r.getMessage() for r in caplog.records)


def test_sentence_contains_evidence():
    assert EVIDENCE in SENTENCE
