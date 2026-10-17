"""Shared objects of the running server, created once in the app's lifespan."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from openai import AsyncOpenAI

from app.config import Settings
from app.db.repository import NotFoundError, Repository
from app.mail.provider import MailProvider
from app.models.project import ProjectStatus
from app.models.run import Run
from app.profile.schema import ProfileSchema
from app.runs.jobs import JobManager
from app.runs.manager import RunManager


@dataclass
class Services:
    settings: Settings
    repository: Repository
    schema: ProfileSchema
    runs: RunManager
    jobs: JobManager
    client: AsyncOpenAI | None
    mail: MailProvider | None = None  # None: the configured provider (tests inject one)

    async def join(self) -> None:
        """Wait until runs and jobs are done (tests, demo scripts)."""
        while self.runs._tasks or self.jobs._tasks:
            await self.runs.join()
            await self.jobs.join()


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


async def company(services: Services, company_id: str) -> tuple[ProjectStatus | None, list[Run]]:
    """Project state and runs (newest first) of a known company; NotFoundError otherwise."""
    state = await services.repository.get_project_state(company_id)
    runs = await services.repository.list_runs(company_id)
    if state is None and not runs:
        raise NotFoundError(f"unknown company '{company_id}'")
    return state, runs
