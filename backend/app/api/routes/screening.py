"""Laws, screening and matches (steps 11 to 15). Ingestion and screening run as background
jobs: the submission answers 202 with the job, polled at its Location."""

import hashlib
import logging
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, Response, UploadFile, status

from app.api.deps import ServicesDep, company
from app.api.errors import ERROR_RESPONSES, ApiError, ErrorCode, ErrorDetail
from app.api.schemas import LawDetail, LawList, MatchList, ScreeningBody, SectionView
from app.db.repository import DuplicateLawError, NotFoundError
from app.models.job import JobOperation, JobView
from app.runs.jobs import law_upload_dir
from app.services.screen import check_state

log = logging.getLogger("app.api")
router = APIRouter(tags=["laws and screening"], responses=ERROR_RESPONSES)

LAWS_PATH = "/api/laws"
PDF_MAGIC = b"%PDF-"


def display_name(filename: str | None) -> str:
    """The original file name, only as a label (the file is stored under a generated name)."""
    name = unicodedata.normalize("NFC", Path((filename or "").replace("\\", "/")).name)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()[:200]
    return name or "upload.pdf"


def _invalid(field: str, message: str) -> ApiError:
    return ApiError(
        422, ErrorCode.VALIDATION, "invalid request", [ErrorDetail(field=field, message=message)]
    )


@router.post(
    LAWS_PATH,
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobView,
    summary="Upload one legal source (PDF) and start its ingestion",
)
async def upload_law(
    services: ServicesDep,
    response: Response,
    file: Annotated[UploadFile, File(description="the legal source as one PDF")],
    replace: Annotated[
        bool, Form(description="replace a stored source with the same content")
    ] = False,
) -> JobView:
    """Stores the PDF under a generated name, then parses, describes, splits and embeds it
    in the background. The same content twice is refused (duplicate) unless `replace`.
    Poll the job; its result is the ingest report with the new law id."""
    services.jobs.require_llm()
    name = display_name(file.filename)
    if not name.casefold().endswith(".pdf"):
        raise _invalid("file", "only PDF files are accepted")
    limit = int(services.settings.max_file_mb * 1024 * 1024)
    job_id = uuid4().hex
    folder = law_upload_dir(services.settings, job_id)
    folder.mkdir(parents=True, exist_ok=False)
    try:
        path, digest, size = folder / "source.pdf", hashlib.sha256(), 0
        with path.open("wb") as target:
            while chunk := await file.read(services.settings.upload_chunk_bytes):
                size += len(chunk)
                if size > limit:
                    raise ApiError(
                        413,
                        ErrorCode.UPLOAD_TOO_LARGE,
                        f"file larger than {services.settings.max_file_mb:g} MB",
                    )
                if size == len(chunk) and not chunk.startswith(PDF_MAGIC):
                    raise _invalid("file", "the file is not a PDF")
                digest.update(chunk)
                target.write(chunk)
        if size == 0:
            raise _invalid("file", "the file is empty")
        content_hash = digest.hexdigest()
        existing = await services.repository.find_law(content_hash)
        if existing is not None and not replace:
            raise DuplicateLawError(
                f"law '{existing.title}' (id {existing.id}) is already stored; send replace"
            )
        job = await services.jobs.submit(
            JobOperation.LAW_INGEST,
            params={"filename": name, "replace": replace, "content_hash": content_hash},
            private={"path": str(path.resolve())},
            job_id=job_id,
        )
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    response.headers["Location"] = f"/api/jobs/{job.id}"
    return job.view()


@router.get(LAWS_PATH, response_model=LawList, summary="Stored legal sources")
async def laws(services: ServicesDep) -> LawList:
    """Metadata, "Zusammenfassung" (`summary`), "Was ändert sich?" (`change_summary`) and the
    number of sections per source."""
    return LawList(laws=await services.repository.list_laws())


@router.get(f"{LAWS_PATH}/{{law_id}}", response_model=LawDetail, summary="One source with sections")
async def law(services: ServicesDep, law_id: int) -> LawDetail:
    """Metadata and every section's text with pages, norms and tax types (no vectors)."""
    found = next((x for x in await services.repository.list_laws() if x.id == law_id), None)
    if found is None:
        raise NotFoundError(f"no law with id {law_id}")
    sections = await services.repository.list_law_sections(law_id)
    return LawDetail(
        law=found,
        sections=[SectionView.model_validate(s.model_dump(exclude={"vector"})) for s in sections],
    )


@router.post(
    "/api/screenings",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobView,
    summary="Start screening (one or every company, one or every law)",
)
async def screen(services: ServicesDep, response: Response, body: ScreeningBody) -> JobView:
    """Reconciles every earlier decision in scope; unchanged judge inputs reuse their
    verdict (no LLM call); at most the configured number of sections is judged per
    execution, the rest is reported as deferred (start another screening). `dry_run`
    stops after scoring and changes nothing. Poll the job for the reports."""
    laws = await services.repository.list_laws()
    if body.law_id is not None and not any(x.id == body.law_id for x in laws):
        raise NotFoundError(f"no law with id {body.law_id}")
    if body.company_id is not None:
        state, _ = await company(services, body.company_id)
        check_state(body.company_id, state)
    if not body.dry_run:
        services.jobs.require_llm()
    job = await services.jobs.submit(
        JobOperation.SCREEN, body.company_id, body.model_dump(exclude={"company_id"})
    )
    response.headers["Location"] = f"/api/jobs/{job.id}"
    return job.view()


@router.get(
    "/api/companies/{company_id}/matches",
    response_model=MatchList,
    summary="Judged matches of a company",
)
async def matches(
    services: ServicesDep,
    company_id: str,
    law_id: int | None = None,
    only_hits: bool = False,
    include_history: bool = False,
) -> MatchList:
    """Current decisions by default; `include_history` adds invalidated ones (with
    `is_current=false` and `invalidation_reason`), whose verdict is history only."""
    await company(services, company_id)
    return MatchList(
        matches=await services.repository.list_matches(
            company_id, only_hits, law_id=law_id, include_history=include_history
        )
    )
