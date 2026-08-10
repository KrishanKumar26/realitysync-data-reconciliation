"""Top-level API router.

Health endpoints are mounted at the root rather than under /api: they are
infrastructure probes, not API resources. Product routers live under /api.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import auth, health, organizations

root_router = APIRouter()
root_router.include_router(health.router)

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(organizations.router)

root_router.include_router(api_router)
