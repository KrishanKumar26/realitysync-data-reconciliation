"""Health and readiness endpoints.

GET /health  — liveness. No dependency checks, always fast. This is what the
               platform health check polls; a Redis outage must not cause a
               restart loop.
GET /ready   — readiness. Probes PostgreSQL and Redis, returns 503 when any
               required dependency is unusable.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.schemas.health import HealthResponse, ReadinessResponse
from app.services.health import collect_readiness

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.api_version,
        environment=settings.environment,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"description": "One or more dependencies are unavailable"}},
)
async def ready(response: Response) -> ReadinessResponse:
    settings = get_settings()
    checks = await collect_readiness(settings.readiness_timeout_seconds)

    database = checks["database"].status
    redis = checks["redis"].status
    is_ready = database == "ok" and redis == "ok"

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        database=database,
        redis=redis,
        checks=checks,
        service=settings.app_name,
        version=settings.api_version,
        environment=settings.environment,
    )
