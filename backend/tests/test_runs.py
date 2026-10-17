"""Step 0 run manager: progress rows, isolation, concurrency rules, restart and rerun."""

import asyncio
import json

import pytest

from app.db.memory import MemoryRepository
from app.db.repository import NotFoundError, RunActiveError, StateError
from app.models.project import ProjectState
from app.models.run import PENDING, Run, RunState
from app.runs.manager import RunManager
from tests.helpers import SENTENCE, FakeClient, answer, entry, make_pdf


def agent(kwargs):
    """Every document names the company; the agent fails on the one called 'broken'."""
    if "response_format" not in kwargs:  # OCR transcription call
        return SENTENCE
    document = kwargs["messages"][-1]["content"]
    if "broken" in document.split("\n")[0]:
        raise RuntimeError("model down")
    return answer(company_name=entry("Muster GmbH", evidence="Muster GmbH"))


def data_room(root, names=("a.pdf", "sub/b.pdf", "broken.pdf")):
    for name in names:
        make_pdf(root / name, [SENTENCE])
    (root / "notes.txt").write_text("not a supported file", "utf-8")
    return root


@pytest.fixture
def repository():
    return MemoryRepository()


def manager(repository, schema, settings, client=None, **updates):
    settings = settings.model_copy(update=updates) if updates else settings
    return RunManager(repository, schema, settings, client or FakeClient(responder=agent))


async def test_pending_rows_before_processing_then_final_statuses(
    repository, schema, settings, tmp_path
):
    folder = data_room(tmp_path / "room")
    runs = manager(repository, schema, settings)

    run = await runs.start("c1", folder, project_name="Projekt")

    rows = await repository.list_run_documents(run.run_id)  # the task has not started yet
    assert [r.relative_path for r in rows] == ["a.pdf", "broken.pdf", "notes.txt", "sub/b.pdf"]
    assert all(r.parse_status == PENDING and r.extraction_status == PENDING for r in rows)

    await runs.join()
    stored = await repository.get_run(run.run_id)
    rows = {r.relative_path: r for r in await repository.list_run_documents(run.run_id)}
    assert stored.state is RunState.FINISHED and stored.published
    assert stored.started_at and stored.finished_at
    assert rows["a.pdf"].parse_status == "ok" and rows["a.pdf"].extraction_status == "ok"
    assert rows["notes.txt"].parse_status == "unsupported"
    assert rows["notes.txt"].extraction_status == "skipped"
    assert rows["broken.pdf"].extraction_status == "failed"  # one failure, run still finished
    assert rows["a.pdf"].pages == 1 and rows["a.pdf"].seconds is not None
    assert stored.counts == {"ok": 2, "failed": 1, "skipped": 1}
    state = await repository.get_project_state("c1")
    assert state.state is ProjectState.PUBLISHED


async def test_rows_follow_completion_order(repository, schema, settings, tmp_path):
    folder = data_room(tmp_path / "room", names=("a.pdf", "b.pdf"))
    runs = manager(repository, schema, settings)
    saved = []
    original = repository.save_run_documents

    async def spy(documents):
        saved.extend((d.relative_path, d.parse_status, d.extraction_status) for d in documents)
        await original(documents)

    repository.save_run_documents = spy
    await runs.start("c1", folder)
    await runs.join()

    for path in ("a.pdf", "b.pdf"):
        steps = [(p, e) for name, p, e in saved if name == path]
        assert steps == [(PENDING, PENDING), ("ok", PENDING), ("ok", "ok")]


async def test_nothing_usable_finishes_without_publish(repository, schema, settings, tmp_path):
    folder = tmp_path / "room"
    folder.mkdir()
    (folder / "notes.txt").write_text("text", "utf-8")
    runs = manager(repository, schema, settings)

    run = await runs.start("c1", folder)
    await runs.join()

    stored = await repository.get_run(run.run_id)
    assert stored.state is RunState.FINISHED and not stored.published
    assert await repository.get_project_state("c1") is None


async def test_one_active_run_per_company_and_global_cap(repository, schema, settings, tmp_path):
    folder = data_room(tmp_path / "room", names=("a.pdf",))
    client = FakeClient(responder=agent, delay=0.3)
    runs = manager(repository, schema, settings, client, max_active_runs=1)

    first = await runs.start("c1", folder)
    with pytest.raises(RunActiveError):
        await runs.start("c1", folder)
    second = await runs.start("c2", folder)
    await asyncio.sleep(0.15)

    assert (await repository.get_run(first.run_id)).state is RunState.RUNNING
    assert (await repository.get_run(second.run_id)).state is RunState.QUEUED
    assert client.completions.max_in_flight == 1
    await runs.join()
    assert (await repository.get_run(second.run_id)).state is RunState.FINISHED


async def test_crash_of_the_run_sets_failed(repository, schema, settings, tmp_path):
    folder = data_room(tmp_path / "room", names=("a.pdf",))
    runs = manager(repository, schema, settings)

    async def broken(*args, **kwargs):
        raise RuntimeError(f"disk full at {settings.data_dir.resolve()}")

    repository.save_profile_draft = broken  # merge still works; publish uses the draft
    repository.publish_profile = broken
    run = await runs.start("c1", folder)
    await runs.join()

    stored = await repository.get_run(run.run_id)
    assert stored.state is RunState.FAILED
    assert "disk full" in stored.last_error and str(settings.data_dir) not in stored.last_error


async def test_restart_marks_active_runs_interrupted(repository, schema, settings):
    await repository.save_run(
        Run(run_id="r1", company_id="c1", state="running", started_by="upload")
    )
    await repository.save_run(
        Run(run_id="r2", company_id="c2", state="queued", started_by="upload")
    )
    await repository.save_run(
        Run(run_id="r3", company_id="c3", state="finished", started_by="upload")
    )

    await manager(repository, schema, settings).startup()

    states = {r.run_id: r.state for r in await repository.list_runs()}
    assert states == {"r1": "interrupted", "r2": "interrupted", "r3": "finished"}


async def test_shutdown_interrupts_running_runs(repository, schema, settings, tmp_path):
    folder = data_room(tmp_path / "room", names=("a.pdf",))
    runs = manager(repository, schema, settings, FakeClient(responder=agent, delay=5))

    run = await runs.start("c1", folder)
    await asyncio.sleep(0.2)
    await runs.shutdown()

    stored = await repository.get_run(run.run_id)
    assert stored.state is RunState.INTERRUPTED and stored.finished_at


async def test_rerun_is_idempotent_and_refused_after_confirm(
    repository, schema, settings, tmp_path
):
    folder = data_room(tmp_path / "room", names=("a.pdf", "b.pdf"))
    runs = manager(repository, schema, settings)
    await runs.start("c1", folder, project_name="Projekt")
    await runs.join()
    before = await repository.list_candidates("c1")

    rerun = await runs.rerun("c1")
    await runs.join()

    assert rerun.started_by == "rerun" and rerun.project_name == "Projekt"
    assert (await repository.get_run(rerun.run_id)).state is RunState.FINISHED
    after = await repository.list_candidates("c1")
    assert len(after) == len(before)  # candidate writes replace, never pile up
    assert len(await repository.list_runs("c1")) == 2

    repository.states["c1"].state = ProjectState.CONFIRMED
    with pytest.raises(StateError):
        await runs.rerun("c1")
    with pytest.raises(NotFoundError):
        await runs.rerun("unknown")


def test_answer_helper_is_json():
    assert json.loads(answer())["company_name"] is None
