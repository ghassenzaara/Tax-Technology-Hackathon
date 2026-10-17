"""Findings 2, 5, 6 and 8: durable cleanup, the shared post-update workflow, background jobs
with explicit recovery, and draft persistence. Memory repository, FakeClient, temp files."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import openai
import psycopg
import pytest

from app.db.repository import BusyError, StateError
from app.models.audit import AuditEntry, AuditKind
from app.models.job import Job, JobOperation, JobStatus
from app.models.project import ProjectState
from app.models.run import Run, RunState
from app.runs.jobs import JobManager
from app.services import cleanup as cleanup_module
from app.services.cleanup import CleanupError, cleanup_project
from app.services.embed import Embedder
from tests.helpers import FakeClient, api_error
from tests.test_embed import GOOD, chat, confirmed
from tests.test_runs import data_room, manager
from tests.test_screen import law, section, verdict


def llm(kwargs):
    """Sentences for step 9, verdicts for the judge."""
    response_format = kwargs.get("response_format")
    if response_format and response_format["json_schema"]["name"] == "law_relevance":
        return verdict()
    return GOOD


def jobs_for(repository, profil_schema, settings, client=None, **updates):
    settings = settings.model_copy(update=updates) if updates else settings
    return JobManager(repository, profil_schema, settings, client or FakeClient(responder=llm))


async def embedded(project, profil_schema, settings):
    repository = await confirmed(project, profil_schema, settings)
    await Embedder(repository, profil_schema, settings, FakeClient(responder=chat(GOOD))).run(
        project
    )
    return repository


async def with_upload(repository, settings, project, *, recorded=True):
    """The API's upload folder plus the run that records it."""
    folder = settings.data_dir / "uploads" / project
    (folder / "DD").mkdir(parents=True)
    (folder / "DD" / "07_Steuerbescheide.pdf").write_bytes(b"%PDF raw")
    await repository.save_run(
        Run(
            run_id=f"run-{project}",
            company_id=project,
            state=RunState.FINISHED,
            folder=str(folder) if recorded else None,
            started_by="upload",
            published=True,
        )
    )
    return folder


async def done(jobs, job_id):
    await jobs.join()
    return await jobs.repository.get_job(job_id)


# Finding 2: durable cleanup ---------------------------------------------------------------


async def test_locked_file_keeps_reference_then_retry_verifies_files(
    profil_schema, settings, project, monkeypatch
):
    repository = await embedded(project, profil_schema, settings)
    folder = await with_upload(repository, settings, project)

    def locked(path, *args, **kwargs):
        raise PermissionError(f"locked: {path}")

    monkeypatch.setattr(cleanup_module.shutil, "rmtree", locked)
    jobs = jobs_for(repository, profil_schema, settings)
    failed = await done(jobs, (await jobs.submit_advance(project)).id)

    assert failed.status is JobStatus.FAILED and "locked" in failed.error
    assert str(settings.data_dir.resolve()) not in failed.error  # sanitized
    state = await repository.get_project_state(project)
    assert state.state is ProjectState.EMBEDDED and "cleanup failed" in state.last_error
    assert folder.exists() and (await repository.list_runs(project))[0].folder == str(folder)
    assert not any((await repository.count_working_rows(project)).values())  # rows went first

    monkeypatch.undo()
    restarted = jobs_for(repository, profil_schema, settings)
    await restarted.startup()
    plan = await restarted.recovery(project)
    assert plan.action == "cleanup" and not plan.needs_llm
    _, job = await restarted.recover(project)
    assert (await done(restarted, job.id)).status is JobStatus.SUCCEEDED
    assert not folder.exists()
    assert (await repository.get_project_state(project)).state is ProjectState.CLEANED
    assert (await repository.list_runs(project))[0].folder is None


async def test_crash_between_rows_and_files_is_recovered(profil_schema, settings, project):
    repository = await embedded(project, profil_schema, settings)
    folder = await with_upload(repository, settings, project)
    await repository.delete_working_data(project)  # then the process died

    jobs = jobs_for(repository, profil_schema, settings)
    await jobs.startup()
    _, job = await jobs.recover(project)

    assert (await done(jobs, job.id)).result["cleanup"]["files_deleted"] == 1
    assert not folder.exists()
    assert (await repository.get_project_state(project)).state is ProjectState.CLEANED


@pytest.mark.parametrize("state", [ProjectState.EMBEDDED, ProjectState.CLEANED])
async def test_cleared_reference_uses_the_deterministic_upload_folder(
    profil_schema, settings, project, state
):
    """Older versions cleared the folder reference before deleting the files."""
    repository = await embedded(project, profil_schema, settings)
    if state is ProjectState.CLEANED:
        await cleanup_project(repository, settings, project)
    folder = await with_upload(repository, settings, project, recorded=False)

    jobs = jobs_for(repository, profil_schema, settings)
    plan, job = await jobs.recover(project)

    assert plan.action == "cleanup"
    assert (await done(jobs, job.id)).status is JobStatus.SUCCEEDED
    assert not folder.exists()
    assert (await repository.get_project_state(project)).state is ProjectState.CLEANED
    assert (await jobs.recovery(project)).action is None  # nothing left


async def test_cleanup_failure_through_the_service(profil_schema, settings, project, monkeypatch):
    repository = await embedded(project, profil_schema, settings)
    folder = await with_upload(repository, settings, project)
    monkeypatch.setattr(
        cleanup_module, "_delete_files", lambda targets: (_ for _ in ()).throw(OSError("busy"))
    )
    with pytest.raises(CleanupError, match="busy"):
        await cleanup_project(repository, settings, project)
    monkeypatch.undo()
    assert (await repository.list_runs(project))[0].folder == str(folder)

    report = await cleanup_project(repository, settings, project)  # retry

    assert report.ok and not folder.exists() and report.files_deleted == [str(folder.resolve())]
    again = await cleanup_project(repository, settings, project)  # already deleted: no-op
    assert again.ok and again.files_deleted == []


# Finding 6: jobs, restart and explicit recovery --------------------------------------------


async def test_interrupted_job_is_never_resumed_and_is_retried_explicitly(
    profil_schema, settings, project
):
    repository = await confirmed(project, profil_schema, settings)
    slow = FakeClient(responder=chat(GOOD), delay=5)
    jobs = jobs_for(repository, profil_schema, settings, slow)
    job = await jobs.submit_advance(project)
    await asyncio.sleep(0.05)
    await jobs.shutdown()  # the server stops
    assert (await repository.get_job(job.id)).status is JobStatus.INTERRUPTED

    # A hard crash would also leave the step marker behind.
    repository.states[project].running_step = "embed"
    repository.states[project].running_since = datetime.now(UTC)
    repository.jobs[job.id].status = JobStatus.RUNNING
    fresh = FakeClient(responder=chat(GOOD))
    restarted = jobs_for(repository, profil_schema, settings, fresh)
    await restarted.startup()
    await asyncio.sleep(0.05)

    assert fresh.completions.calls == fresh.embeddings.calls == 0  # no automatic LLM work
    assert (await repository.get_job(job.id)).status is JobStatus.INTERRUPTED
    state = await repository.get_project_state(project)
    assert state.running_step is None and state.state is ProjectState.CONFIRMED
    assert (await restarted.recovery(project)).action == "embed_and_cleanup"

    attempt = await restarted.retry(job.id)
    assert attempt.retry_of == job.id and attempt.id != job.id
    assert (await done(restarted, attempt.id)).status is JobStatus.SUCCEEDED
    assert (await repository.get_project_state(project)).state is ProjectState.CLEANED
    with pytest.raises(StateError, match="already retried"):
        await restarted.retry(job.id)
    with pytest.raises(StateError, match="only failed or interrupted"):
        await restarted.retry(attempt.id)


async def test_exhausted_embedding_retries_then_recovery(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)
    broken = FakeClient(
        responder=chat(GOOD), embedding_errors=[api_error(openai.InternalServerError, 500)] * 10
    )
    jobs = jobs_for(repository, profil_schema, settings, broken)
    failed = await done(jobs, (await jobs.submit_advance(project)).id)

    assert failed.status is JobStatus.FAILED and "EmbedError" in failed.error
    state = await repository.get_project_state(project)
    assert state.state is ProjectState.CONFIRMED and "embed failed" in state.last_error
    with pytest.raises(StateError, match="after confirmation|rerun"):
        await jobs_for(repository, profil_schema, settings).recover("unknown-company")

    recovering = jobs_for(repository, profil_schema, settings)
    plan, job = await recovering.recover(project)
    assert plan.action == "embed_and_cleanup"
    assert (await done(recovering, job.id)).status is JobStatus.SUCCEEDED
    assert (await repository.get_project_state(project)).state is ProjectState.CLEANED


async def test_recovery_follows_the_state(profil_schema, settings, project):
    repository = await embedded(project, profil_schema, settings)
    jobs = jobs_for(repository, profil_schema, settings)
    assert (await jobs.recovery(project)).action == "cleanup"
    repository.states[project].embeddings_stale = True
    assert (await jobs.recovery(project)).action == "rebuild_vectors"
    _, job = await jobs.recover(project)
    result = (await done(jobs, job.id)).result
    assert result["embedded"]["written"] > 0 and "cleanup" in result
    state = await repository.get_project_state(project)
    assert state.state is ProjectState.CLEANED and not state.embeddings_stale

    plan, none = await jobs.recover(project)  # cleaned with fresh vectors
    assert none is None and plan.reason == "nothing to recover"
    repository.states[project].state = ProjectState.PUBLISHED
    with pytest.raises(StateError, match="rerun"):
        await jobs.recover(project)


async def test_concurrent_recovery_requests_start_one_job(profil_schema, settings, project):
    repository = await embedded(project, profil_schema, settings)
    jobs = jobs_for(repository, profil_schema, settings)

    results = await asyncio.gather(
        jobs.recover(project), jobs.recover(project), return_exceptions=True
    )

    started = [r for r in results if not isinstance(r, BaseException)]
    assert len(started) == 1 and isinstance(
        next(r for r in results if isinstance(r, BaseException)), BusyError
    )
    await jobs.join()
    assert len(await repository.list_jobs(project)) == 1


async def test_duplicate_active_jobs_are_refused(profil_schema, settings, project):
    repository = await confirmed(project, profil_schema, settings)
    jobs = jobs_for(repository, profil_schema, settings, FakeClient(responder=llm, delay=0.2))
    await jobs.submit_advance(project)
    with pytest.raises(BusyError, match="already active"):
        await jobs.submit_refresh(project, set())
    assert await repository.company_busy(project, 3600)
    await jobs.join()
    assert await repository.company_busy(project, 3600) is None


async def test_stale_lock_timeout_applies_to_step_markers(profil_schema, settings, project):
    repository = await embedded(project, profil_schema, settings)
    repository.states[project].running_step = "embed"
    repository.states[project].running_since = datetime.now(UTC) - timedelta(hours=2)
    assert await repository.company_busy(project, 3600) is None  # abandoned
    repository.states[project].running_since = datetime.now(UTC)
    assert await repository.company_busy(project, 3600) == "embed is running"


# Finding 5: the shared post-update workflow ------------------------------------------------


async def refresh_ready(profil_schema, settings, project):
    """Embedded and screened once, then an update marked the vectors stale."""
    repository = await embedded(project, profil_schema, settings)
    vector = [1.0] * settings.embedding_dimension
    await repository.save_law(law(), [section(settings, vector=vector)], replace=False)
    repository.audit.append(
        AuditEntry(
            id=len(repository.audit) + 1,
            kind=AuditKind.PROFILE_CHANGED,
            company_id=project,
            acted_by=settings.advisor_label,
        )
    )
    repository.states[project].embeddings_stale = True
    return repository


@pytest.mark.parametrize("enabled", [True, False])
async def test_refresh_job_screens_only_when_configured(profil_schema, settings, project, enabled):
    repository = await refresh_ready(profil_schema, settings, project)
    client = FakeClient(responder=llm)
    jobs = jobs_for(repository, profil_schema, settings, client, screen_after_update=enabled)

    job = await done(jobs, (await jobs.submit_refresh(project, set())).id)

    assert job.status is JobStatus.SUCCEEDED
    assert not (await repository.get_project_state(project)).embeddings_stale
    pending = await repository.pending_profile_changes(project)
    if enabled:
        assert job.result["screening"]["complete"] and pending == []
        assert await repository.list_matches(project)
    else:
        assert job.result["screening"] is None and pending
        assert await repository.list_matches(project) == []
        assert (await repository.screening_status(project)).pending_changes == 1


async def test_failed_refresh_skips_screening(profil_schema, settings, project):
    repository = await refresh_ready(profil_schema, settings, project)
    broken = FakeClient(
        responder=llm, embedding_errors=[api_error(openai.InternalServerError, 500)] * 10
    )
    jobs = jobs_for(repository, profil_schema, settings, broken, screen_after_update=True)
    repository.embeddings[project][0].text_basis = "geändert"  # force a new vector
    repository.states[project].embeddings_stale = True

    job = await done(jobs, (await jobs.submit_refresh(project, {"finding:1"})).id)

    assert job.status is JobStatus.FAILED
    assert (await repository.get_project_state(project)).embeddings_stale
    judge_calls = [r for r in broken.completions.requests if r.get("response_format") is not None]
    assert judge_calls == [] and await repository.pending_profile_changes(project)
    assert (await jobs.recovery(project)).action == "rebuild_vectors"


async def test_failed_screening_keeps_markers_and_error(profil_schema, settings, project):
    repository = await refresh_ready(profil_schema, settings, project)
    await cleanup_project(repository, settings, project)

    async def broken(changes):
        raise RuntimeError("database gone")

    repository.apply_screening = broken
    jobs = jobs_for(repository, profil_schema, settings, screen_after_update=True)

    job = await done(jobs, (await jobs.submit_refresh(project, set())).id)

    assert job.status is JobStatus.FAILED and "re-screening failed" in job.error
    state = await repository.get_project_state(project)
    assert not state.embeddings_stale and "re-screening failed" in state.last_error
    assert await repository.pending_profile_changes(project)
    assert (await jobs.recovery(project)).action == "screen_pending"

    del repository.apply_screening  # the database is back
    _, job = await jobs.recover(project)
    result = (await done(jobs, job.id)).result
    assert result["screening"]["complete"] and "embedded" not in result
    assert await repository.pending_profile_changes(project) == []
    assert (await jobs.recovery(project)).action is None


# Finding 8: draft persistence -------------------------------------------------------------


async def test_draft_write_failure_fails_the_run(repository, schema, settings, tmp_path):
    folder = data_room(tmp_path / "room", names=("a.pdf",))
    runs = manager(repository, schema, settings)
    attempts = []

    async def failing(draft):
        attempts.append(draft)
        raise RuntimeError("db down")

    repository.save_profile_draft = failing
    run = await runs.start("c1", folder)
    await runs.join()

    stored = await repository.get_run(run.run_id)
    assert stored.state is RunState.FAILED and not stored.published
    assert len(attempts) == settings.db_write_attempts
    assert await repository.get_project_state("c1") is None  # nothing published
    assert await repository.get_profile_draft("c1") is None


async def test_transient_draft_write_is_retried(repository, schema, settings, tmp_path):
    folder = data_room(tmp_path / "room", names=("a.pdf",))
    runs = manager(repository, schema, settings)
    save = repository.save_profile_draft
    attempts = []

    async def flaky(draft):
        attempts.append(draft)
        if len(attempts) == 1:
            raise RuntimeError("connection reset")
        await save(draft)

    repository.save_profile_draft = flaky
    run = await runs.start("c1", folder)
    await runs.join()

    stored = await repository.get_run(run.run_id)
    assert stored.state is RunState.FINISHED and stored.published and len(attempts) == 2
    assert await repository.get_profile_draft("c1") is not None


@pytest.fixture
def repository():
    from app.db.memory import MemoryRepository

    return MemoryRepository()


# Send guard and update lock -----------------------------------------------------------------


async def test_send_guard_refuses_stale_vectors_and_active_work(settings, project):
    from app.services import email
    from app.services.screen import Screener
    from tests.test_outreach import FakeMail, request
    from tests.test_screen import ready

    repository = await ready(settings, project)
    await Screener(repository, settings, FakeClient(text=verdict())).screen(project)
    (alert,) = await repository.list_alerts(project)

    repository.states[project].embeddings_stale = True
    with pytest.raises(StateError, match="stale vectors"):
        await email.prepare_email(repository, settings, alert.id)
    with pytest.raises(StateError, match="stale vectors"):
        await email.send_email(repository, settings, request(alert), FakeMail())
    repository.states[project].embeddings_stale = False

    jobs = JobManager(repository, None, settings, FakeClient(text=verdict(), delay=0.2))
    await jobs.submit(JobOperation.SCREEN, project, {})
    with pytest.raises(BusyError, match="busy"):
        await email.prepare_email(repository, settings, alert.id)
    mail = FakeMail()
    with pytest.raises(BusyError):
        await email.send_email(repository, settings, request(alert), mail)
    assert mail.calls == [] and await repository.list_outreach(project) == []
    await jobs.join()
    assert (await email.send_email(repository, settings, request(alert), mail)).status == "sent"


async def test_update_lock_respects_the_timeout_and_jobs_postgres(
    pg_repository, profil_schema, settings, project
):
    from app.models.update import Change, ChangeKind, UpdateRequest
    from app.services.update import update_profile
    from tests.test_update import pg_confirmed

    await pg_confirmed(pg_repository, project, profil_schema, settings)

    def change(version):
        return UpdateRequest(
            company_id=project,
            profile_version=version,
            changed_by=settings.advisor_label,
            changes=[Change(field="finanzamt", kind=ChangeKind.EDIT, value=f"FA {version}")],
        )

    url = os.environ["TEST_DATABASE_URL"]
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute(
            "UPDATE pipeline.project_state SET running_step = 'embed', "
            "running_since = now() - interval '2 hours' WHERE company_id = %s",
            (project,),
        )
    # An abandoned marker (older than the timeout) does not block the update.
    result = await update_profile(pg_repository, profil_schema, settings, change(1), None)
    assert result.profile_version == 2

    await pg_repository.begin_step(project, "embed", settings.step_lock_timeout_s)
    with pytest.raises(BusyError, match="embed"):
        await update_profile(pg_repository, profil_schema, settings, change(2), None)
    await pg_repository.end_step(project)

    job = await pg_repository.create_job(
        Job(id=uuid.uuid4().hex, operation="advance", company_id=project, dedupe_key=project)
    )
    try:
        with pytest.raises(BusyError, match="job"):
            await update_profile(pg_repository, profil_schema, settings, change(2), None)
        with pytest.raises(BusyError, match="already active"):
            await pg_repository.create_job(
                Job(
                    id=uuid.uuid4().hex, operation="refresh", company_id=project, dedupe_key=project
                )
            )
        assert await pg_repository.company_busy(project, 3600)
    finally:
        job.status = JobStatus.FAILED
        await pg_repository.save_job(job)
    assert (await pg_repository.get_job(job.id)).status is JobStatus.FAILED
    result = await update_profile(pg_repository, profil_schema, settings, change(2), None)
    assert result.profile_version == 3
