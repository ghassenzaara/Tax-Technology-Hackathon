"""Plan section 4b service: the advisor updates a confirmed profile ("Update entries").

The update itself is one transaction in the repository (validation, advisor provenance,
stamps, closed hints, history, profile version, invalidated screening decisions). The
post-update workflow (`refresh_and_screen`) runs after the commit and is shared by the CLI
(right away) and the HTTP API (a background refresh job): refresh the affected vectors;
with `screen_after_update`, then re-screen the pending changes. A failed refresh leaves
"embeddings stale" and skips screening; a failed screening keeps its audit markers
pending. Recovery (`--advance`, or the API's recover action) repairs both.
"""

import logging
from datetime import date

from openai import AsyncOpenAI

from app.config import Settings
from app.db.repository import Repository
from app.models.match import ScreenReport
from app.models.update import ConfirmedProfile, UpdateRequest, UpdateResult
from app.profile.schema import ProfileSchema
from app.services.embed import Embedder
from app.services.screen import Screener

log = logging.getLogger(__name__)


class ScreeningAfterUpdateError(RuntimeError):
    """The vectors were refreshed, but the configured re-screening failed."""


async def refresh_and_screen(
    repository: Repository,
    schema: ProfileSchema,
    settings: Settings,
    client: AsyncOpenAI,
    company_id: str,
    changed: set[str],
) -> ScreenReport | None:
    """Refresh the vectors by diff, then (if configured) screen the pending changes.
    Raises EmbedError (no screening then) or ScreeningAfterUpdateError."""
    await Embedder(repository, schema, settings, client).refresh(company_id, changed)
    if not settings.screen_after_update:
        return None
    try:
        return await Screener(repository, settings, client).screen_pending(company_id)
    except Exception as exc:
        raise ScreeningAfterUpdateError(f"re-screening failed: {exc}") from exc


async def update_profile(
    repository: Repository,
    schema: ProfileSchema,
    settings: Settings,
    request: UpdateRequest,
    client: AsyncOpenAI | None,
    today: date | None = None,
    *,
    refresh: bool = True,
) -> UpdateResult:
    """Apply the changes (all or nothing), then run the post-update workflow. With
    `refresh=False` (the HTTP API, which runs it as a background job) the result keeps
    `embeddings_stale` set and the caller starts the job.

    Raises UpdateError (every problem per field), StateError, BusyError or
    StaleProfileError; nothing is changed then."""
    result = await repository.update_profile(request, schema, settings, today or date.today())
    log.info(
        "updated project=%s by=%s changes=%d version=%d hints_closed=%d",
        request.company_id,
        request.changed_by,
        result.changes,
        result.profile_version,
        result.hints_closed,
    )
    if not result.embeddings_stale or not refresh:
        return result  # not embedded yet (step 9 embeds the updated profile), or deferred
    if client is None:
        result.refresh_error = "no OPENAI_API_KEY: vectors stay stale until --advance"
        return result
    try:
        report = await refresh_and_screen(
            repository, schema, settings, client, request.company_id, set(result.changed_features)
        )
    except ScreeningAfterUpdateError as exc:  # the change stays committed; markers pending
        log.warning("re-screening failed for %s: %s", request.company_id, exc)
        result.embeddings_refreshed, result.embeddings_stale = True, False
        result.screening_error = str(exc)
        return result
    except Exception as exc:  # the change stays committed; the stale flag stays set
        log.warning("embedding refresh failed for %s: %s", request.company_id, type(exc).__name__)
        result.refresh_error = str(exc)
        return result
    result.embeddings_refreshed, result.embeddings_stale = True, False
    if report is not None:
        result.screening = report.summary()
    return result


async def read_profile(
    repository: Repository, schema: ProfileSchema, settings: Settings, company_id: str
) -> ConfirmedProfile:
    """The confirmed profile with citations, version, stale flag and history."""
    return await repository.read_profile(company_id, schema, settings)
