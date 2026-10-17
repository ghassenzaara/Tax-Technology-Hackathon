"""HTTP API and upload layer (step 0): a small FastAPI server for the front half of the flow.

The API holds no business logic: routes validate input, call the existing services
(orchestrator, confirm, embed, cleanup, update, repository) through the run manager or
directly, and shape the result. Start it with `uv run python -m app.api` (see
`app/api/__main__.py` for the Windows event loop) or build it with `create_app()`.

No authentication: single-user platform (one Steuerberater).
TODO: only if the platform ever becomes multi-user, add login and record the actor.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI

from app.api.deps import Services
from app.api.errors import ErrorCode, install_error_handlers
from app.api.middleware import RequestContext
from app.api.routes import profile, review, system, uploads
from app.config import Settings, get_settings
from app.db.repository import Repository, make_repository
from app.llm import make_client
from app.profile.schema import ProfileSchema, load_profile_schema
from app.runs.manager import RunManager

log = logging.getLogger("app.api")

DESCRIPTION = f"""Backend of the DD regulatory monitoring platform (steps 0 to 10).

Flow: Upload, poll Progress every 1 to 2 seconds until the run is finished, Draft, Confirm
(embedding and cleanup then run in the background; Progress shows the project state),
Profile, Update, Audit. Ids are opaque strings. No authentication (single user).

Every error has the shape `{{code, message, details, error_id}}`. Codes:
{", ".join(f"`{c.value}`" for c in ErrorCode)}.
"""
MULTIPART_OVERHEAD = 16 * 1024 * 1024  # headers of every part on top of the file bytes


def create_app(
    settings: Settings | None = None,
    *,
    repository: Repository | None = None,
    schema: ProfileSchema | None = None,
    client: AsyncOpenAI | None = None,
) -> FastAPI:
    """The app. Tests pass a repository and a fake client; the server builds both from the
    settings. Without OPENAI_API_KEY uploads still run, but extraction fails per document."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo = repository or make_repository(settings)
        await repo.initialize()  # pipeline tables, idempotent
        if settings.repository == "postgres":
            applied = await repo.initialize_profile_schema(settings.profile_db_dir)
            if applied:
                log.info("schema profil created: %s", ", ".join(applied))
        llm = client if client is not None else make_client(settings)
        if llm is None:
            log.warning("no OPENAI_API_KEY: extraction and embeddings will fail")
        profile_schema = schema or load_profile_schema(settings.profile_schema_path)
        services = Services(
            settings=settings,
            repository=repo,
            schema=profile_schema,
            runs=RunManager(repo, profile_schema, settings, llm),
            client=llm,
        )
        await services.runs.startup()
        app.state.services = services
        try:
            yield
        finally:
            await services.runs.shutdown()
            await repo.close()

    app = FastAPI(
        title="DD Monitor API",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    install_error_handlers(app)
    for router in (system.router, uploads.router, review.router, profile.router):
        app.include_router(router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(
        RequestContext,
        upload_path=uploads.UPLOAD_PATH,
        upload_limit=int(settings.upload_max_total_mb * 1024 * 1024) + MULTIPART_OVERHEAD,
    )
    return app
