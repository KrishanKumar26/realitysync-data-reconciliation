"""Response models for the health and readiness endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ComponentState = Literal["ok", "error"]


class ComponentCheck(BaseModel):
    """Diagnostic detail for a single dependency."""

    status: ComponentState
    latency_ms: float | None = Field(
        default=None, description="Round-trip time of the probe, in milliseconds."
    )
    error: str | None = Field(
        default=None,
        description="Safe, non-sensitive failure summary. Never contains a DSN or credential.",
    )


class HealthResponse(BaseModel):
    """Liveness: is this process alive? No dependencies are consulted."""

    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    """Readiness: are required dependencies usable?"""

    status: Literal["ready", "not_ready"]
    database: ComponentState
    redis: ComponentState
    checks: dict[str, ComponentCheck]
    service: str
    version: str
    environment: str
