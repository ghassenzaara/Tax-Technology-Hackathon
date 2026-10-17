"""Step 8 service: review read and the advisor's confirm (called by the CLI, later the API).

Confirm is never chained automatically: it needs the advisor's decisions. All checks that
need no database run first (state, draft version, decisions), then the repository repeats
state and version under a lock and republishes the confirmed draft in one transaction.
"""

import logging
from datetime import date
from time import perf_counter

from app.config import Settings
from app.db.repository import Repository, StaleDraftError, StateError
from app.models.project import Confirmation, ProjectState
from app.models.review import ConfirmRequest, ConfirmResult, ReviewView
from app.profile.review import apply_decisions, draft_version, needs_review_labels, open_decisions
from app.profile.schema import ProfileSchema

log = logging.getLogger(__name__)


async def review_profile(
    repository: Repository, schema: ProfileSchema, settings: Settings, company_id: str
) -> ReviewView:
    """The stored draft, its version, the open decisions, the state and open hints."""
    draft = await repository.get_profile_draft(company_id)
    if draft is None:
        raise StateError(f"project '{company_id}' has no profile draft (run --extract first)")
    state = await repository.get_project_state(company_id)
    return ReviewView(
        company_id=company_id,
        state=state.state if state else None,
        draft_version=draft_version(draft),
        draft=draft,
        open_decisions=open_decisions(draft, schema, settings.rounding_max_granularity),
        needs_review=needs_review_labels(draft),
        open_hints=await repository.list_open_hints(company_id),
    )


async def confirm_profile(
    repository: Repository,
    schema: ProfileSchema,
    settings: Settings,
    request: ConfirmRequest,
    today: date | None = None,
) -> ConfirmResult:
    """Apply the decisions and confirm, all or nothing. Raises StateError, StaleDraftError,
    DecisionError (every problem per field) or PublishError; the state then stays published."""
    start = perf_counter()
    cid = request.company_id
    state = await repository.get_project_state(cid)
    if state is None or state.state is not ProjectState.PUBLISHED:
        current = state.state if state else "not published"
        raise StateError(f"project '{cid}' is {current}: confirm needs state published")
    draft = await repository.get_profile_draft(cid)
    if draft is None or draft_version(draft) != request.draft_version:
        raise StaleDraftError(f"draft version {request.draft_version} is stale; reload the review")
    confirmed, result = apply_decisions(draft, schema, request, settings.rounding_max_granularity)
    # The documents of the last publish keep their dokument rows.
    extractions = [
        e for e in await repository.list_extractions(cid) if e.doc_id in state.published_docs
    ]
    counts = result.model_dump(
        include={
            "fields_accepted",
            "conflicts_resolved",
            "values_typed",
            "fields_left_empty",
            "rows_dropped",
        }
    )
    confirmation = Confirmation(
        by=request.confirmed_by,
        on=today or date.today(),
        draft_version=request.draft_version,
        counts=counts,  # recorded in the profile_confirmed audit entry
    )
    await repository.begin_step(cid, "confirm", settings.step_lock_timeout_s)
    try:
        report = await repository.confirm_profile(
            confirmed, extractions, schema, settings, confirmation
        )
    except Exception as exc:
        await repository.record_error(cid, f"confirm failed: {exc}")
        raise
    finally:
        await repository.end_step(cid)
    result.rows = report.rows
    result.hints_closed = report.hints_closed
    result.hints_open = report.hints - report.hints_closed
    log.info(
        "confirmed project=%s by=%s conflicts=%d typed=%d empty=%d dropped=%d "
        "hints_closed=%d hints_open=%d in %.1f seconds",
        cid,
        request.confirmed_by,
        result.conflicts_resolved,
        result.values_typed,
        result.fields_left_empty,
        result.rows_dropped,
        result.hints_closed,
        result.hints_open,
        perf_counter() - start,
    )
    return result
