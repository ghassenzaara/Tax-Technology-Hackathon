"""Plan section 4b service: the advisor updates a confirmed profile ("Update entries").

The update itself is one transaction in the repository (validation, advisor provenance,
stamps, closed hints, history, profile version). The embedding refresh runs after the
commit: if it fails, the change stays committed and the project keeps the "embeddings
stale" flag; `--advance` (a full embed) repairs it. The later HTTP API runs the refresh in
the background; the CLI runs it right away.
"""

import logging
from datetime import date

from openai import AsyncOpenAI

from app.config import Settings
from app.db.repository import Repository
from app.models.update import ConfirmedProfile, UpdateRequest, UpdateResult
from app.profile.schema import ProfileSchema
from app.services.embed import Embedder

log = logging.getLogger(__name__)


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
    """Apply the changes (all or nothing), then refresh the affected vectors. With
    `refresh=False` (the HTTP API, which refreshes in the background) the result keeps
    `embeddings_stale` set and the caller starts the refresh.

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
        await Embedder(repository, schema, settings, client).refresh(
            request.company_id, set(result.changed_features)
        )
    except Exception as exc:  # the change stays committed; the stale flag stays set
        log.warning("embedding refresh failed for %s: %s", request.company_id, type(exc).__name__)
        result.refresh_error = str(exc)
        return result
    result.embeddings_refreshed, result.embeddings_stale = True, False
    return result


async def read_profile(
    repository: Repository, schema: ProfileSchema, settings: Settings, company_id: str
) -> ConfirmedProfile:
    """The confirmed profile with citations, version, stale flag and history."""
    return await repository.read_profile(company_id, schema, settings)
