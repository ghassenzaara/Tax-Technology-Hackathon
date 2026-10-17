"""Same behavior on every repository. Postgres runs only with TEST_DATABASE_URL set, e.g.
`TEST_DATABASE_URL=postgresql://dd:dd@localhost:5432/dd` after `docker compose up -d`."""

import os
import uuid

import pytest

from app.config import Settings
from app.db.memory import MemoryRepository
from app.db.repository import RepositoryError, make_repository
from app.models.extraction import Candidate, DocumentExtraction, ExtractionStatus
from app.models.profile import FieldResult, FieldStatus, ProfileDraft

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(params=["memory", "postgres"])
async def repository(request):
    if request.param == "memory":
        yield MemoryRepository()
        return
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    from app.db.postgres import PostgresRepository

    repo = PostgresRepository(DATABASE_URL)
    await repo.initialize()
    yield repo
    await repo.close()


@pytest.fixture
def company_id():
    return f"test-{uuid.uuid4().hex[:12]}"  # isolates runs on a shared database


def extraction(company_id, doc_id, values, status=ExtractionStatus.OK):
    return DocumentExtraction(
        company_id=company_id,
        doc_id=doc_id,
        filename=f"{doc_id}.pdf",
        status=status,
        candidates=[
            Candidate(
                company_id=company_id,
                field_name="employees",
                value=value,
                evidence=f"{value} Mitarbeiter",
                source_doc=doc_id,
                source_filename=f"{doc_id}.pdf",
                source_page=1,
                confidence=0.9,
            )
            for value in values
        ],
        warnings=["w"],
    )


async def test_replace_twice_no_duplicates(repository, company_id):
    await repository.replace_document_candidates(extraction(company_id, "a", [25, 26]))
    await repository.replace_document_candidates(extraction(company_id, "b", [30]))
    await repository.replace_document_candidates(extraction(company_id, "a", [27]))

    rows = await repository.list_candidates(company_id)
    assert sorted((c.source_doc, c.value) for c in rows) == [("a", 27), ("b", 30)]


async def test_candidate_round_trip(repository, company_id):
    original = extraction(company_id, "a", [{"nested": [1, "x"]}]).candidates[0]
    await repository.replace_document_candidates(
        extraction(company_id, "a", []).model_copy(update={"candidates": [original]})
    )

    (stored,) = await repository.list_candidates(company_id)
    assert stored.model_dump(exclude={"created_at"}) == original.model_dump(exclude={"created_at"})


@pytest.mark.parametrize("status", [ExtractionStatus.FAILED, ExtractionStatus.SKIPPED])
async def test_failed_rerun_keeps_previous_candidates(repository, company_id, status):
    # Review finding 2: an OpenAI outage in run 2 must not erase run 1's facts or rank.
    good = extraction(company_id, "a", [25]).model_copy(update={"document_class": "bescheid"})
    await repository.replace_document_candidates(good)

    failed = extraction(company_id, "a", [], status=status)
    await repository.replace_document_candidates(failed)

    assert [c.value for c in await repository.list_candidates(company_id)] == [25]
    (record,) = await repository.list_extractions(company_id)
    assert record.status is status and record.document_class == "bescheid"


async def test_extraction_records_round_trip(repository, company_id):
    record = extraction(company_id, "a", [25]).model_copy(
        update={
            "document_class": "bescheid",
            "document_title": "KSt-Bescheid 2024",
            "document_date": "2026-02-03",
        }
    )
    await repository.replace_document_candidates(record)

    (stored,) = await repository.list_extractions(company_id)
    assert stored.candidates == [] and stored.status is ExtractionStatus.OK
    assert (stored.document_class, stored.document_date) == ("bescheid", "2026-02-03")
    assert stored.warnings == ["w"]


async def test_draft_round_trip_latest_wins(repository, company_id):
    assert await repository.get_profile_draft(company_id) is None
    for value in (1, 2):
        draft = ProfileDraft(
            company_id=company_id,
            fields=[FieldResult(name="employees", status=FieldStatus.SINGLE_SOURCE, value=value)],
            counts={FieldStatus.SINGLE_SOURCE: 1},
            schema_fields=["employees"],
        )
        await repository.save_profile_draft(draft)

    latest = await repository.get_profile_draft(company_id)
    assert latest.field("employees").value == 2


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")
async def test_failure_mid_write_keeps_previous_rows(company_id):
    from app.db.postgres import PostgresRepository

    repo = PostgresRepository(DATABASE_URL)
    await repo.initialize()
    try:
        await repo.replace_document_candidates(extraction(company_id, "a", [25]))
        bad = extraction(company_id, "a", [26])
        bad.candidates[0].confidence = 5.0  # violates the CHECK constraint after the DELETE
        with pytest.raises(Exception):  # noqa: B017 - any DB error
            await repo.replace_document_candidates(bad)

        rows = await repo.list_candidates(company_id)
        assert [c.value for c in rows] == [25]
    finally:
        await repo.close()


def test_factory(settings):
    assert isinstance(make_repository(settings), MemoryRepository)
    with pytest.raises(RepositoryError, match="DATABASE_URL"):
        make_repository(Settings(_env_file=None, repository="postgres", database_url=None))
