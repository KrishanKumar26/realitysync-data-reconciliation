"""Top-level API router.

Health endpoints are mounted at the root rather than under /api: they are
infrastructure probes, not API resources. Product routers live under /api.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    auth,
    dashboard,
    data_sources,
    health,
    organizations,
    reality,
    system,
)

root_router = APIRouter()
root_router.include_router(health.router)

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(data_sources.router)
api_router.include_router(reality.router)
api_router.include_router(dashboard.router)
api_router.include_router(system.router)

root_router.include_router(api_router)
