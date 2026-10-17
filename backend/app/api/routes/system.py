"""Health: server alive and database reachable."""

import logging

from fastapi import APIRouter

from app.api.deps import ServicesDep
from app.api.errors import ERROR_RESPONSES, ApiError, ErrorCode
from app.api.schemas import Health

log = logging.getLogger("app.api")
router = APIRouter(tags=["system"], responses=ERROR_RESPONSES)


@router.get(
    "/api/health",
    response_model=Health,
    responses={503: ERROR_RESPONSES[500] | {"description": "database_unavailable"}},
)
async def health(services: ServicesDep) -> Health:
    try:
        await services.repository.ping()
    except Exception as exc:
        log.warning("health: database unreachable: %s", type(exc).__name__)
        raise ApiError(503, ErrorCode.DATABASE_UNAVAILABLE, "database unreachable") from exc
    return Health(status="ok", database="ok")
