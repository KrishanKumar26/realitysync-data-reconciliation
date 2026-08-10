"""Audit trail writes.

Every audit row goes through this module so that redaction happens in one
place. Callers pass whatever context is useful; anything that looks like a
secret is stripped before it reaches the database, using the same rules the
log pipeline applies. An audit log that quietly stores a password is worse
than no audit log, because it is trusted.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.redaction import redact
from app.models.audit_log import AuditLog

#: Bounds what a caller can push into the JSONB column. Audit rows are written
#: on hot paths like login; an unbounded dict is an availability risk.
_MAX_DETAIL_KEYS = 20
_MAX_DETAIL_VALUE_CHARS = 500


def _clean_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    redacted = redact(details)
    if not isinstance(redacted, dict):  # pragma: no cover - redact preserves dicts
        return {}
    trimmed: dict[str, Any] = {}
    for key, value in list(redacted.items())[:_MAX_DETAIL_KEYS]:
        if isinstance(value, str) and len(value) > _MAX_DETAIL_VALUE_CHARS:
            value = value[:_MAX_DETAIL_VALUE_CHARS] + "…"
        trimmed[str(key)] = value
    return trimmed


def client_ip(request: Request | None) -> str | None:
    """Best-effort client address.

    Only ``request.client`` is trusted. X-Forwarded-For is attacker-controlled
    unless a known proxy is stripping and rewriting it, and treating it as
    truth would let anyone write whatever address they like into the audit
    trail. Wiring a trusted-proxy configuration belongs with deployment.
    """
    if request is None or request.client is None:
        return None
    return request.client.host


def user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    value = request.headers.get("user-agent")
    return value[:1000] if value else None


def request_id_of(request: Request | None) -> str | None:
    if request is None:
        return None
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


async def record(
    db: AsyncSession,
    *,
    action: str,
    organization_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AuditLog:
    """Append an audit row.

    Added to the session but not committed: the caller owns the transaction, so
    an audit row lands only if the action it describes also landed.
    """
    entry = AuditLog(
        action=action,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        details=_clean_details(details),
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        request_id=request_id_of(request),
    )
    db.add(entry)
    return entry
