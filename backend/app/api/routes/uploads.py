"""Upload, Rerun and Progress (step 0). Thin: the upload layer and the run manager do the work."""

import logging
import shutil
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status

from app.api.deps import ServicesDep, company
from app.api.errors import ERROR_RESPONSES, ApiError, ErrorCode, ErrorDetail
from app.api.schemas import (
    DocumentProgress,
    ProgressResponse,
    ProjectView,
    RecoveryView,
    RunView,
    UploadResponse,
)
from app.runs.manager import redact
from app.runs.upload import IncomingFile, new_company, store_files
from app.services.screen import SCREENABLE

log = logging.getLogger("app.api")
router = APIRouter(tags=["upload and progress"], responses=ERROR_RESPONSES)

UPLOAD_PATH = "/api/uploads"


@router.post(
    UPLOAD_PATH,
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadResponse,
    summary="Upload a data room and start the run",
)
async def upload(
    services: ServicesDep,
    files: Annotated[
        list[UploadFile] | None,
        File(description="every file of the DD folder (repeat the field once per file)"),
    ] = None,
    paths: Annotated[
        list[str] | None,
        Form(
            description="relative path of each file, same order as `files` (for example "
            "'Steuern/Bescheide/KSt 2022.pdf'); without it the file name of each part is used"
        ),
    ] = None,
    project_name: Annotated[
        str | None, Form(description="optional project name; the company id is made from it")
    ] = None,
) -> UploadResponse:
    """Stores every file below a new company folder (relative paths kept), creates the
    company id and starts the run in the background. Unsupported file types are accepted and
    reported later as unsupported; unsafe paths, duplicates and files over a limit are
    rejected one by one with a reason. Poll Progress every 1 to 2 seconds."""
    files = files or []
    if not files:
        raise ApiError(400, ErrorCode.EMPTY_UPLOAD, "the upload contains no file")
    if paths and len(paths) != len(files):
        raise ApiError(
            422,
            ErrorCode.VALIDATION,
            "invalid request",
            [ErrorDetail(field="paths", message="one relative path per file is required")],
        )
    names = paths or [f.filename or "" for f in files]
    settings = services.settings
    company_id, folder = await new_company(services.repository, settings, project_name)
    try:
        accepted, rejected = await store_files(
            [IncomingFile(name, f.read) for name, f in zip(names, files, strict=True)],
            folder,
            settings,
        )
        if not accepted:
            raise ApiError(
                400,
                ErrorCode.EMPTY_UPLOAD,
                "no file of the upload was accepted",
                [ErrorDetail(field=r.relative_path, message=r.reason) for r in rejected],
            )
        run = await services.runs.start(
            company_id, folder, project_name=project_name, started_by="upload"
        )
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)  # frees the company id again
        raise
    log.info(
        "upload company=%s files accepted=%d rejected=%d bytes=%d",
        company_id,
        len(accepted),
        len(rejected),
        sum(a.size for a in accepted),
    )
    return UploadResponse(
        company_id=company_id,
        run_id=run.run_id,
        project_name=project_name,
        accepted=accepted,
        rejected=rejected,
    )


@router.post(
    "/api/companies/{company_id}/rerun",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunView,
    summary="Process the stored files again (before confirmation only)",
)
async def rerun(services: ServicesDep, company_id: str) -> RunView:
    run = await services.runs.rerun(company_id)
    return RunView.model_validate(run, from_attributes=True)


@router.get(
    "/api/companies/{company_id}/progress",
    response_model=ProgressResponse,
    summary="Project state, latest run and one record per document",
)
async def progress(services: ServicesDep, company_id: str) -> ProgressResponse:
    state, runs = await company(services, company_id)
    project = ProjectView()
    if state is not None:
        project = ProjectView.model_validate(state, from_attributes=True)
        if state.last_error:
            project.last_error = redact(state.last_error, services.settings)
    run = runs[0] if runs else None
    documents = await services.repository.list_run_documents(run.run_id) if run else []
    jobs = await services.repository.list_jobs(company_id)
    active = next((j for j in jobs if j.status.active), None)
    plan = await services.jobs.recovery(company_id)
    screening = None
    if state is not None and state.state in SCREENABLE:
        screening = await services.repository.screening_status(company_id)
    return ProgressResponse(
        company_id=company_id,
        project=project,
        run=RunView.model_validate(run, from_attributes=True) if run else None,
        documents=[DocumentProgress.model_validate(d, from_attributes=True) for d in documents],
        active_job=active.view() if active else None,
        latest_job=jobs[0].view() if jobs else None,
        recovery=RecoveryView(available=plan.available, action=plan.action, reason=plan.reason),
        screening=screening,
    )
