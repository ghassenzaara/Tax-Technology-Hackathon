"""Background jobs: poll, explicit retry, and the state-selected recovery of a company.
Nothing is retried automatically (a restart only marks jobs interrupted)."""

from fastapi import APIRouter, Response, status

from app.api.deps import ServicesDep, company
from app.api.errors import ERROR_RESPONSES
from app.api.schemas import RecoveryResponse
from app.db.repository import NotFoundError
from app.models.job import JobView

router = APIRouter(tags=["jobs"], responses=ERROR_RESPONSES)


@router.get("/api/jobs/{job_id}", response_model=JobView, summary="Poll a background job")
async def job(services: ServicesDep, job_id: str) -> JobView:
    """Status queued, running, succeeded, failed or interrupted; `result` when done,
    a sanitized `error` when failed. Poll every 1 to 2 seconds."""
    found = await services.repository.get_job(job_id)
    if found is None:
        raise NotFoundError(f"no job with id {job_id}")
    return found.view()


@router.post(
    "/api/jobs/{job_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobView,
    summary="Retry a failed or interrupted job (a new attempt)",
)
async def retry(services: ServicesDep, response: Response, job_id: str) -> JobView:
    """The new attempt links `retry_of` and checks the current state when it runs. Never
    resends an email (sending is not a job)."""
    attempt = await services.jobs.retry(job_id)
    response.headers["Location"] = f"/api/jobs/{attempt.id}"
    return attempt.view()


@router.post(
    "/api/companies/{company_id}/recover",
    response_model=RecoveryResponse,
    responses={202: {"model": RecoveryResponse, "description": "recovery job started"}},
    summary="Finish interrupted or failed work after confirmation",
)
async def recover(services: ServicesDep, response: Response, company_id: str) -> RecoveryResponse:
    """Selects the work from the current state: confirmed -> embed and clean up; stale
    vectors -> rebuild them (and clean up if needed); embedded -> clean up; cleaned ->
    nothing, unless files are left or configured screening is pending. Answers 202 with
    the job, or 200 without one when there is nothing to do. Never repeats the
    confirmation and never sends an email; before confirmation use rerun."""
    await company(services, company_id)
    plan, started = await services.jobs.recover(company_id)
    if started is None:
        return RecoveryResponse(action=None, reason=plan.reason)
    response.status_code = status.HTTP_202_ACCEPTED
    response.headers["Location"] = f"/api/jobs/{started.id}"
    return RecoveryResponse(action=plan.action, reason=plan.reason, job=started.view())
