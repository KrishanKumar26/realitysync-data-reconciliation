"""RealitySync API application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import root_router
from app.cache.redis import close_redis
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.middleware.errors import register_exception_handlers
from app.middleware.request_id import REQUEST_ID_HEADER, RequestIDMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage process-wide resources.

    Connections are created lazily on first use rather than eagerly at
    startup, so the API can boot and serve /health while a dependency is
    still coming up. /ready is the endpoint that reports the truth.
    """
    settings = get_settings()
    logger.info(
        "app.startup",
        environment=settings.environment,
        version=settings.api_version,
        cors_origins=settings.cors_origins,
    )
    try:
        yield
    finally:
        await dispose_engine()
        await close_redis()
        logger.info("app.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application."""
    settings = settings or get_settings()

    configure_logging(
        level=settings.log_level,
        environment=settings.environment,
        service="realitysync-api",
    )

    app = FastAPI(
        title=settings.app_name,
        version=settings.api_version,
        description=(
            "RealitySync reconciles observations from multiple data sources into a "
            "continuously verified reality state. Phase 1 exposes health endpoints only."
        ),
        lifespan=lifespan,
        # Interactive docs are useful in development and noise (plus an
        # information leak) in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Order matters: RequestIDMiddleware is added last so it runs first and a
    # request id is bound before CORS or any handler produces output.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER, "Idempotency-Key"],
        expose_headers=[REQUEST_ID_HEADER],
        max_age=600,
    )
    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)
    app.include_router(root_router)

    return app


app = create_app()
