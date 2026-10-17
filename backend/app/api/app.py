"""HTTP API (steps 0 to 18): a small FastAPI server for the whole advisor workflow.

The API holds no business logic: routes validate input, call the existing services
(orchestrator, confirm, embed, cleanup, update, ingestion, screening, alerts, email,
repository) through the run manager, the job manager or directly, and shape the result.
Start it with `uv run python -m app.api` (see `app/api/__main__.py` for the Windows event
loop) or build it with `create_app()`.

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
from app.api.middleware import RequestContext, RequestGuard
from app.api.routes import jobs, outreach, profile, review, screening, system, uploads
from app.config import Settings, get_settings
from app.db.repository import Repository, make_repository
from app.llm import make_client
from app.mail.provider import MailProvider
from app.profile.schema import ProfileSchema, load_profile_schema
from app.runs.jobs import JobManager
from app.runs.manager import RunManager

log = logging.getLogger("app.api")

DESCRIPTION = f"""Backend of the DD regulatory monitoring platform (steps 0 to 18).

Flow: Upload, poll Progress every 1 to 2 seconds until the run is finished, Draft, Confirm
(embedding and cleanup then run as a background job; Progress shows it), Profile, Update,
Audit. Legal sources: upload a PDF (a job), list them. Screening: start a job, poll it,
read the company's matches. Alerts: list, open, mark seen or dismiss; preview the email,
edit the template, send the confirmed email, read the outreach history.

Long work answers 202 with a job (`Location: /api/jobs/{{id}}`); poll it. Failed or
interrupted jobs are retried explicitly; Recover selects the work from the project state.
Nothing is resumed automatically after a restart. Calendar is not part of the platform.
Ids are opaque. No authentication (single user).

Every error has the shape `{{code, message, details, error_id, outreach_id}}`. Codes:
{", ".join(f"`{c.value}`" for c in ErrorCode)}.
"""
MULTIPART_OVERHEAD = 16 * 1024 * 1024  # headers of every part on top of the file bytes
LAW_OVERHEAD = 64 * 1024  # one file part and the replace field


def create_app(
    settings: Settings | None = None,
    *,
    repository: Repository | None = None,
    schema: ProfileSchema | None = None,
    client: AsyncOpenAI | None = None,
    mail: MailProvider | None = None,
) -> FastAPI:
    """The app. Tests pass a repository, a fake client and a fake mail transport; the server
    builds them from the settings. Without OPENAI_API_KEY uploads still run, but extraction
    fails per document and LLM work answers llm_unavailable."""
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
            jobs=JobManager(repo, profile_schema, settings, llm),
            client=llm,
            mail=mail,
        )
        await services.runs.startup()
        await services.jobs.startup()  # interrupted jobs stay interrupted: no LLM call
        app.state.services = services
        try:
            yield
        finally:
            await services.runs.shutdown()
            await services.jobs.shutdown()
            await repo.close()

    app = FastAPI(
        title="DD Monitor API",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    install_error_handlers(app)
    routers = (
        system.router,
        uploads.router,
        review.router,
        profile.router,
        screening.router,
        outreach.router,
        jobs.router,
    )
    for router in routers:
        app.include_router(router)
    # Added innermost first: RequestContext -> CORS -> RequestGuard -> handlers and routes.
    app.add_middleware(
        RequestGuard,
        limits={
            uploads.UPLOAD_PATH: int(settings.upload_max_total_mb * 1024 * 1024)
            + MULTIPART_OVERHEAD,
            screening.LAWS_PATH: int(settings.max_file_mb * 1024 * 1024) + LAW_OVERHEAD,
        },
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Location"],
    )
    app.add_middleware(RequestContext)
    return app
