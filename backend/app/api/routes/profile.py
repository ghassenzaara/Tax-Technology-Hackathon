"""Companies, Profile, Update and Audit (after confirmation, plan section 4b)."""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ServicesDep, company
from app.api.errors import ERROR_RESPONSES
from app.api.schemas import AuditList, CompanyList, CompanySummary, UpdateBody
from app.db.repository import StateError
from app.models.audit import AuditFilter, AuditKind
from app.models.project import EMBEDDABLE
from app.models.update import ConfirmedProfile, UpdateRequest, UpdateResult
from app.services.update import read_profile, update_profile

log = logging.getLogger("app.api")
router = APIRouter(tags=["profile"], responses=ERROR_RESPONSES)


@router.get("/api/companies", response_model=CompanyList, summary="The client list")
async def companies(services: ServicesDep) -> CompanyList:
    repository = services.repository
    runs = {}
    for run in await repository.list_runs():  # newest first: keep the latest per company
        runs.setdefault(run.company_id, run)
    states = {s.company_id: s for s in await repository.list_project_states()}
    summaries = []
    for cid in sorted(runs.keys() | states.keys()):
        run, state = runs.get(cid), states.get(cid)
        times = [
            t
            for t in (
                run and run.created_at,
                run and run.finished_at,
                state and state.published_at,
                state and state.confirmed_at,
                state and state.embedded_at,
                state and state.cleaned_at,
            )
            if t
        ]
        summaries.append(
            CompanySummary(
                company_id=cid,
                project_name=run.project_name if run else None,
                state=state.state if state else None,
                run_state=run.state if run else None,
                embeddings_stale=bool(state and state.embeddings_stale),
                created_at=min(times, default=None),
                updated_at=max(times, default=None),
            )
        )
    return CompanyList(companies=summaries)


@router.get(
    "/api/companies/{company_id}/profile",
    response_model=ConfirmedProfile,
    summary="The confirmed profile with citations and its version",
)
async def profile(services: ServicesDep, company_id: str) -> ConfirmedProfile:
    """Values with their citation (file and page in `fundstelle`, the cited document in
    `quelle`; advisor-entered values say so instead), the profile version needed by Update,
    the "embeddings stale" flag and the audit log. Never evidence quotes or conflict
    alternatives."""
    state, _ = await company(services, company_id)
    if state is None or state.state not in EMBEDDABLE:
        current = state.state if state else "not published"
        raise StateError(f"project '{company_id}' is {current}: the profile is not confirmed yet")
    return await read_profile(services.repository, services.schema, services.settings, company_id)


@router.post(
    "/api/companies/{company_id}/profile/update",
    response_model=UpdateResult,
    summary="Update entries of a confirmed profile",
)
async def update(services: ServicesDep, company_id: str, body: UpdateBody) -> UpdateResult:
    """All or nothing; there is no undo (ask "are you sure" first). Refused before
    confirmation (wrong_state), while a step or job of the company runs (busy) and on a
    stale profile version (stale_profile); invalid values come back per field. The vectors
    are refreshed by a background job (then, with SCREEN_AFTER_UPDATE, the changes are
    re-screened); Progress shows the job and "embeddings stale" until then. A contact
    address edit needs neither."""
    await company(services, company_id)
    request = UpdateRequest(
        company_id=company_id,
        profile_version=body.profile_version,
        changed_by=services.settings.advisor_label,
        reason=body.reason,
        changes=body.changes,
    )
    result = await update_profile(
        services.repository, services.schema, services.settings, request, None, refresh=False
    )
    if result.embeddings_stale:
        try:
            await services.jobs.submit_refresh(company_id, set(result.changed_features))
        except Exception as exc:  # the change stands; Progress offers recovery
            log.warning("refresh job of %s not started: %s", company_id, type(exc).__name__)
    return result


@router.get("/api/audit", response_model=AuditList, summary="The audit log, newest first")
async def audit(
    services: ServicesDep,
    company_id: str | None = None,
    kind: AuditKind | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> AuditList:
    """Confirmations, profile changes (field, old and new value, reason), sent emails and
    template edits: what and when."""
    entries = await services.repository.list_audit(
        AuditFilter(company_id=company_id, kind=kind, since=since, until=until, limit=limit)
    )
    return AuditList(entries=entries)
