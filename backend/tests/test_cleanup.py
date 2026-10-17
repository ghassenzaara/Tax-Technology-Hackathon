"""Step 10: delete working data after the vectors exist; verification; path safety; logs
never carry document text. Postgres end to end skipped without TEST_DATABASE_URL."""

import logging
import os

import pytest

from app.agents.orchestrator import Orchestrator
from app.db.repository import StateError
from app.models.project import ProjectState
from app.services.cleanup import CleanupError, cleanup_project
from app.services.confirm import confirm_profile
from app.services.embed import Embedder
from tests.helpers import FakeClient, answer, entry, make_document
from tests.profil_data import QUOTE, all_profile_text, decide_all, with_conflict
from tests.test_confirm import published
from tests.test_embed import GOOD, chat, confirmed


async def embedded(project, profil_schema, settings):
    repository = await confirmed(project, profil_schema, settings)
    client = FakeClient(responder=chat(GOOD))
    await Embedder(repository, profil_schema, settings, client).run(project)
    return repository


def upload(settings, name="upload"):
    """A fake upload folder inside the data directory, with a raw file and scan markdown."""
    folder = settings.data_dir / name
    folder.mkdir(parents=True)
    (folder / "07_Steuerbescheide.pdf").write_bytes(b"%PDF raw")
    (folder / "scan.md").write_text(f"{QUOTE} transcribed page", "utf-8")
    return folder


async def test_cleanup_deletes_rows_and_files(profil_schema, settings, project):
    repository = await embedded(project, profil_schema, settings)
    folder = upload(settings)

    report = await cleanup_project(repository, settings, project, [folder])

    assert report.ok and report.state is ProjectState.CLEANED
    assert report.rows_deleted["profile_drafts"] >= 1
    assert report.rows_deleted["document_extractions"] == 5
    assert all(n == 0 for n in report.rows_left.values())
    assert not folder.exists() and report.files_deleted == [str(folder.resolve())]
    assert (await repository.get_project_state(project)).state is ProjectState.CLEANED
    assert repository.published[project] and len(repository.embeddings[project]) == 3  # kept

    again = await cleanup_project(repository, settings, project, [folder])  # no-op
    assert again.ok and again.files_deleted == [] and again.state is ProjectState.CLEANED


async def test_refused_before_embedding(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)

    with pytest.raises(StateError, match="cleanup needs state embedded"):
        await cleanup_project(repository, settings, project)

    assert await repository.get_profile_draft(project) is not None


async def test_refused_with_incomplete_embeddings(profil_schema, settings, project):
    repository = await embedded(project, profil_schema, settings)
    repository.embeddings[project].pop()  # a vector went missing

    with pytest.raises(CleanupError, match="embedding set incomplete"):
        await cleanup_project(repository, settings, project)

    assert (await repository.get_project_state(project)).state is ProjectState.EMBEDDED
    assert (await repository.count_working_rows(project))["profile_drafts"] >= 1


@pytest.mark.parametrize("outside", ["..", "data", "../elsewhere"])
async def test_paths_outside_data_dir_are_refused(profil_schema, settings, project, outside):
    repository = await embedded(project, profil_schema, settings)
    folder = upload(settings)
    target = settings.data_dir / outside if outside != "data" else settings.data_dir

    with pytest.raises(CleanupError, match="outside the data directory"):
        await cleanup_project(repository, settings, project, [folder, target])

    assert folder.exists()  # nothing deleted, not even the allowed path
    assert (await repository.get_project_state(project)).state is ProjectState.EMBEDDED


async def test_failing_delete_keeps_state_embedded(profil_schema, settings, project, monkeypatch):
    repository = await embedded(project, profil_schema, settings)

    async def broken(company_id):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(repository, "delete_working_data", broken)

    with pytest.raises(CleanupError, match="disk on fire"):
        await cleanup_project(repository, settings, project)

    state = await repository.get_project_state(project)
    assert state.state is ProjectState.EMBEDDED and "cleanup failed" in state.last_error


async def test_rows_left_after_delete_keep_state_embedded(
    profil_schema, settings, project, monkeypatch
):
    repository = await embedded(project, profil_schema, settings)

    async def nothing(company_id):  # a delete that silently leaves rows behind
        return {}

    monkeypatch.setattr(repository, "delete_working_data", nothing)

    with pytest.raises(CleanupError, match="cleanup incomplete"):
        await cleanup_project(repository, settings, project)

    assert (await repository.get_project_state(project)).state is ProjectState.EMBEDDED


async def test_extraction_logs_never_contain_evidence(schema, settings, caplog):
    # Plan 4.4: log lines carry file names and counts only, never document text.
    evidence = f"{QUOTE} Restaurant in Berlin mit 25 Mitarbeitern"
    client = FakeClient(text=answer(employees=entry(25, evidence=evidence)))
    docs = [make_document("a.pdf", [evidence]), make_document("b.pdf", ["leer " * 10])]

    async def stream():
        for doc in docs:
            yield doc

    with caplog.at_level(logging.DEBUG):
        await Orchestrator(schema, _memory(), settings, client).run(stream(), "c1")

    assert "a.pdf" in caplog.text and QUOTE not in caplog.text


def _memory():
    from app.db.memory import MemoryRepository

    return MemoryRepository()


# Postgres: publish -> confirm -> embed -> cleanup -----------------------------------------


async def test_full_flow_postgres(pg_repository, profil_schema, settings, project):
    view = await published(pg_repository, project, profil_schema, settings, with_conflict(project))
    await confirm_profile(pg_repository, profil_schema, settings, decide_all(view))
    await Embedder(pg_repository, profil_schema, settings, FakeClient(responder=chat(GOOD))).run(
        project
    )
    folder = upload(settings)

    report = await cleanup_project(pg_repository, settings, project, [folder])

    assert report.ok and report.state is ProjectState.CLEANED
    assert await pg_repository.count_working_rows(project) == {
        "field_candidates": 0,
        "document_extractions": 0,
        "profile_drafts": 0,
        "run_documents": 0,
    }
    state = await pg_repository.get_project_state(project)
    assert state.state is ProjectState.CLEANED and state.confirmed_by == "Dr. Test"
    keys = await pg_repository.list_embedding_keys(project, settings.embedding_model)
    assert len(keys) == 3  # profil and the vectors stay
    texts = await all_profile_text(os.environ["TEST_DATABASE_URL"], project)
    assert len(texts) > 20 and not any(QUOTE in t for t in texts)  # no evidence quote left
