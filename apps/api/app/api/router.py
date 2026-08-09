"""Top-level API router.

Health endpoints are mounted at the root rather than under /api: they are
infrastructure probes, not API resources. Product routers land under /api in
Phase 2 and later.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import health

root_router = APIRouter()
root_router.include_router(health.router)
