"""The camelCase Klaris data adapter API. Legacy /api routes remain available."""

import shutil
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile

from app.api.deps import ServicesDep, company
from app.api.errors import ERROR_RESPONSES, ApiError, ErrorCode, ErrorDetail
from app.api.routes.uploads import progress
from app.db.repository import BusyError, StateError
from app.models.audit import AuditKind
from app.models.job import JobOperation
from app.models.outreach import DEFAULT_BODY, DEFAULT_SUBJECT
from app.models.project import EMBEDDABLE
from app.runs.upload import IncomingFile, new_company, store_files
from app.services.cleanup import upload_folder
from app.ui import contract as c
from app.ui.service import UiService

router = APIRouter(prefix="/api/ui", tags=["Klaris"], responses=ERROR_RESPONSES)
UPLOAD_PATH = "/api/ui/uploads"


@router.get("/advisor", response_model=c.Advisor)
async def advisor(s: ServicesDep):
    return await UiService(s).advisor()


@router.get("/companies", response_model=list[c.Company])
async def companies(s: ServicesDep):
    return await UiService(s).companies()


@router.get("/companies/drafts", response_model=list[c.Company])
async def drafts(s: ServicesDep):
    return await UiService(s).companies(drafts=True)


@router.get("/companies/{cid}", response_model=c.Company)
async def get_company(s: ServicesDep, cid: str):
    return await UiService(s).get_company(cid)


@router.delete("/companies/{cid}", status_code=204)
async def discard(s: ServicesDep, cid: str):
    await UiService(s).discard(cid)
    return Response(status_code=204)


@router.post("/companies/{cid}/update", response_model=c.Company)
async def update(s: ServicesDep, cid: str, body: c.CompanyUpdate):
    return await UiService(s).update(cid, body)


@router.post("/companies/{cid}/notes", response_model=c.Company)
async def note(s: ServicesDep, cid: str, body: c.NoteBody):
    return await UiService(s).note(cid, body.notes)


@router.post("/uploads", response_model=c.UploadRun, status_code=202)
async def upload_files(
    s: ServicesDep,
    files: Annotated[list[UploadFile] | None, File()] = None,
    paths: Annotated[list[str] | None, Form()] = None,
    company_id: Annotated[str | None, Form(alias="companyId")] = None,
):
    service = UiService(s)
    if not company_id:
        if not files:
            raise ApiError(400, ErrorCode.EMPTY_UPLOAD, "Keine Dateien ausgewählt.")
        if paths and len(paths) != len(files):
            raise ApiError(
                422, ErrorCode.VALIDATION_ERROR, "Für jede Datei wird ein Pfad benötigt."
            )
        cid, folder = await new_company(s.repository, s.settings, None)
        try:
            accepted, rejected = await store_files(
                [
                    IncomingFile(name, f.read)
                    for name, f in zip(
                        paths or [f.filename or "" for f in files], files, strict=True
                    )
                ],
                folder,
                s.settings,
            )
            if rejected:
                raise ApiError(
                    422,
                    ErrorCode.VALIDATION_ERROR,
                    "Einige Dateien wurden abgelehnt.",
                    [ErrorDetail(field=r.relative_path, message=r.reason) for r in rejected],
                )
            if not accepted:
                raise ApiError(400, ErrorCode.EMPTY_UPLOAD, "Keine Datei wurde angenommen.")
            started = await s.runs.start(cid, folder, started_by="ui_upload")
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return await service.run(started.run_id)
    await service.idle(company_id)
    state, _ = await company(s, company_id)
    if not state or state.state not in EMBEDDABLE:
        raise StateError("Neue Dokumente können nur zu bestätigten Mandanten hinzugefügt werden.")
    if state.embeddings_stale or str(state.state) != "cleaned":
        raise BusyError("Bitte die laufende Profilverarbeitung zuerst abschließen.")
    if await s.repository.get_profile_draft(company_id):
        raise BusyError("Bitte zuerst den vorhandenen Dokumentenentwurf prüfen.")
    if not files:
        raise ApiError(400, ErrorCode.EMPTY_UPLOAD, "Keine Dateien ausgewählt.")
    if paths and len(paths) != len(files):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, "Für jede Datei wird ein Pfad benötigt.")
    folder = upload_folder(s.settings, company_id)
    if folder is None:
        raise StateError("Für diesen Mandanten kann kein Uploadordner angelegt werden.")
    await s.repository.begin_step(company_id, "upload", s.settings.step_lock_timeout_s)
    staging_owned = False
    run_started = False
    try:
        if folder.exists():
            raise BusyError("Es sind noch Dateien aus einem vorherigen Upload vorhanden.")
        staging_owned = True
        accepted, rejected = await store_files(
            [
                IncomingFile(name, f.read)
                for name, f in zip(paths or [f.filename or "" for f in files], files, strict=True)
            ],
            folder,
            s.settings,
        )
        if rejected:
            # Partial uploads must never silently hide rejected files from the advisor.
            raise ApiError(
                422,
                ErrorCode.VALIDATION_ERROR,
                "Einige Dateien wurden abgelehnt.",
                [ErrorDetail(field=r.relative_path, message=r.reason) for r in rejected],
            )
        if not accepted:
            raise ApiError(400, ErrorCode.EMPTY_UPLOAD, "Keine Datei wurde angenommen.")
        run = await s.runs.start(company_id, folder, started_by="update_dd")
        run_started = True
        await s.repository.append_audit(
            service.audit(
                company_id,
                AuditKind.DOCUMENTS_ADDED,
                new_value={"files": len(accepted), "run_id": run.run_id},
            )
        )
    except BaseException:
        # Only remove this request's unprocessed staging folder, never an earlier upload.
        if staging_owned and not run_started:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        await s.repository.end_step(company_id)
    return await service.run(run.run_id)


@router.get("/runs/{run_id}", response_model=c.UploadRun)
async def run(s: ServicesDep, run_id: str):
    return await UiService(s).run(run_id)


@router.get("/companies/{cid}/draft", response_model=c.ProfileDraft)
async def draft(s: ServicesDep, cid: str):
    return await UiService(s).draft(cid)


@router.post("/companies/{cid}/confirm", response_model=c.Company)
async def confirm(s: ServicesDep, cid: str, body: c.ConfirmBody):
    return await UiService(s).confirm(cid, body)


@router.get("/law-changes", response_model=list[c.LawChange])
async def laws(s: ServicesDep):
    return await UiService(s).laws()


@router.get("/law-changes/{law_id}", response_model=c.LawChange)
async def law(s: ServicesDep, law_id: int):
    return await UiService(s).law(law_id)


@router.post("/law-changes/{law_id}/seen", status_code=204)
async def seen(s: ServicesDep, law_id: int):
    await s.repository.mark_law_seen(law_id)
    return Response(status_code=204)


@router.post("/screening-runs", response_model=c.Job, status_code=202)
async def screening(s: ServicesDep, response: Response):
    s.jobs.require_llm()
    job = await s.jobs.submit(JobOperation.SCREENING_RUN)
    response.headers["Location"] = f"/api/ui/jobs/{job.id}"
    return job_contract(job)


@router.get("/screening-runs/latest", response_model=c.Job | None)
async def latest_screening(s: ServicesDep):
    jobs = await s.repository.list_jobs()
    job = next((j for j in jobs if j.operation is JobOperation.SCREENING_RUN), None)
    return job_contract(job) if job else None


@router.post("/companies/{cid}/rerun", response_model=c.UploadRun, status_code=202)
async def rerun(s: ServicesDep, cid: str):
    from pathlib import Path

    await UiService(s).idle(cid)
    state, runs = await company(s, cid)
    if state and state.state in EMBEDDABLE:
        if not runs or runs[0].started_by != "update_dd" or not runs[0].folder:
            raise StateError("Keine neuen Dokumente zur erneuten Verarbeitung vorhanden.")
        run = await s.runs.start(cid, Path(runs[0].folder), started_by="update_dd")
    else:
        previous = runs[0] if runs else None
        if previous and previous.folder:
            run = await s.runs.start(cid, Path(previous.folder), started_by="ui_rerun")
        else:
            run = await s.runs.rerun(cid)
    return await UiService(s).run(run.run_id)


def job_contract(job) -> c.Job:
    return c.Job.model_validate(job.model_dump(mode="json"))


@router.get("/jobs/{job_id}", response_model=c.Job)
async def get_job(s: ServicesDep, job_id: str):
    job = await s.repository.get_job(job_id)
    if job is None:
        UiService.missing("Verarbeitung")
    return job_contract(job)


@router.post("/jobs/{job_id}/retry", response_model=c.Job, status_code=202)
async def retry(s: ServicesDep, job_id: str):
    return job_contract(await s.jobs.retry(job_id))


@router.get("/companies/{cid}/processing", response_model=c.Processing)
async def processing(s: ServicesDep, cid: str):
    value = await progress(s, cid)
    return c.Processing(
        company_id=cid,
        state=value.project.state,
        embeddings_stale=value.project.embeddings_stale,
        active_job=job_contract(value.active_job) if value.active_job else None,
        latest_job=job_contract(value.latest_job) if value.latest_job else None,
        recovery=c.Recovery(
            available=value.recovery.available,
            action=value.recovery.action,
            reason=value.recovery.reason,
        ),
        screening_pending=bool(value.screening and value.screening.pending),
        update_pending=await s.repository.get_profile_draft(cid) is not None
        and value.project.state in EMBEDDABLE
        and bool(value.run and value.run.started_by == "update_dd"),
    )


@router.post("/companies/{cid}/recover", response_model=c.Job | None)
async def recover(s: ServicesDep, cid: str):
    _, runs = await company(s, cid)
    if runs and runs[0].started_by == "update_dd" and await s.repository.get_profile_draft(cid):
        raise StateError("Bitte zuerst den Dokumentenentwurf prüfen.")
    _, job = await s.jobs.recover(cid)
    return job_contract(job) if job else None


@router.get("/matches", response_model=list[c.Match])
async def matches(
    s: ServicesDep,
    company_id: Annotated[str | None, Query(alias="companyId")] = None,
    law_id: Annotated[int | None, Query(alias="lawChangeId")] = None,
):
    return await UiService(s).matches(company_id, law_id)


@router.post("/matches/{match_id}/review", response_model=c.Match)
async def review(s: ServicesDep, match_id: str, body: c.MatchReviewPatch):
    return await UiService(s).review(match_id, body)


@router.get("/companies/{cid}/email-template", response_model=c.EmailTemplate)
async def client_template(s: ServicesDep, cid: str):
    return await UiService(s).client_template(cid)


@router.get("/email-template", response_model=c.StoredEmailTemplate)
async def template(s: ServicesDep):
    return await UiService(s).template()


@router.post("/email-template", response_model=c.StoredEmailTemplate)
async def edit_template(s: ServicesDep, body: c.TemplateBody):
    return await UiService(s).edit_template(body)


@router.post("/email-template/reset", response_model=c.StoredEmailTemplate)
async def reset_template(s: ServicesDep):
    return await UiService(s).edit_template(
        c.TemplateBody(subject=DEFAULT_SUBJECT, body=DEFAULT_BODY), reset=True
    )


@router.get("/email-template/history", response_model=list[c.TemplateHistoryEntry])
async def template_history(s: ServicesDep):
    return await UiService(s).template_history()


@router.post("/emails", response_model=c.Outreach)
async def send(s: ServicesDep, body: c.SendEmailBody):
    return await UiService(s).send(body)


@router.get("/companies/{cid}/outreach", response_model=list[c.Outreach])
async def outreach(s: ServicesDep, cid: str):
    return await UiService(s).outreach(cid)


@router.get("/companies/{cid}/audit", response_model=list[c.AuditEntry])
async def audit(s: ServicesDep, cid: str):
    return await UiService(s).history(cid)
