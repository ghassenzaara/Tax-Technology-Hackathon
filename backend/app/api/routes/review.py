"""Draft and Confirm (step 8). After a successful confirm, embedding and cleanup (steps 9
and 10) run as a background job; Progress reports it (and offers recovery if it fails)."""

import logging

from fastapi import APIRouter

from app.api.deps import Services, ServicesDep, company
from app.api.errors import ERROR_RESPONSES
from app.api.schemas import ConfirmBody
from app.db.repository import RunActiveError, StateError
from app.models.project import ProjectState
from app.models.review import ConfirmRequest, ConfirmResult, ReviewView
from app.models.run import RunState
from app.services.confirm import confirm_profile, review_profile

log = logging.getLogger("app.api")
router = APIRouter(tags=["review"], responses=ERROR_RESPONSES)


async def _reviewable(services: Services, company_id: str) -> None:
    """The latest run is finished and the project waits for review (state published)."""
    state, runs = await company(services, company_id)
    if services.runs.is_active(company_id) or (runs and runs[0].state.active):
        raise RunActiveError(f"a run of '{company_id}' is still active; wait for it")
    if runs and runs[0].state is not RunState.FINISHED:
        raise StateError(f"the latest run of '{company_id}' is {runs[0].state}; start a rerun")
    if state is None:
        raise StateError(f"project '{company_id}' has no profile draft (no usable document)")
    if state.state is not ProjectState.PUBLISHED:
        raise StateError(
            f"project '{company_id}' is {state.state}: the draft is only available for review "
            "before confirmation"
        )


@router.get(
    "/api/companies/{company_id}/draft",
    response_model=ReviewView,
    summary="The profile draft for review, with sources and evidence quotes",
)
async def draft(services: ServicesDep, company_id: str) -> ReviewView:
    """Fields with status, candidate groups with sources and evidence quotes (untrusted
    document text: render as plain text), needs-review flags, non-binding suggestions, list
    rows, the draft version, the open decisions and the open hints. Only in state
    published after a finished run."""
    await _reviewable(services, company_id)
    return await review_profile(services.repository, services.schema, services.settings, company_id)


@router.post(
    "/api/companies/{company_id}/confirm",
    response_model=ConfirmResult,
    summary="Confirm the profile with the advisor's decisions",
)
async def confirm(services: ServicesDep, company_id: str, body: ConfirmBody) -> ConfirmResult:
    """All or nothing. Every open decision of the Draft call needs one decision (nothing
    is pre-selected). Problems come back per field (code validation); a newer draft answers
    stale_draft. On success embedding and cleanup start in the background."""
    await _reviewable(services, company_id)
    request = ConfirmRequest(
        company_id=company_id,
        draft_version=body.draft_version,
        confirmed_by=services.settings.advisor_label,
        decisions=body.decisions,
    )
    result = await confirm_profile(services.repository, services.schema, services.settings, request)
    try:
        await services.jobs.submit_advance(company_id)
    except Exception as exc:  # the confirmation stands; Progress offers recovery
        log.warning("advance job of %s not started: %s", company_id, type(exc).__name__)
    return result
