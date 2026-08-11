"""Operational status contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: `disabled` is a deployment choice, not a fault. Keeping it distinct from
#: `degraded` means an operator scanning the response can tell "someone turned
#: this off on purpose" from "this broke".
ComponentState = Literal["operational", "degraded", "down", "disabled"]


class ComponentStatus(BaseModel):
    """One subsystem's state, with a sentence an operator can act on."""

    name: str
    state: ComponentState
    detail: str = Field(
        description="Human-readable explanation. Never contains credentials, "
        "connection strings, or tenant data."
    )


class SystemStatusResponse(BaseModel):
    """Deployment-wide operational view.

    Contains no tenant data, which is why it is authenticated but not
    organization-scoped.
    """

    status: ComponentState
    environment: str
    version: str
    checked_at: datetime
    components: list[ComponentStatus]
