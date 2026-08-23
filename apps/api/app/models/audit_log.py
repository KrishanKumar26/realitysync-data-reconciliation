"""Audit log — an append-only record of security-relevant events.

``organization_id`` is deliberately nullable. The events most worth auditing
are often the ones with no tenant context: a failed login for an address that
matches no account, a registration, a logout after the membership that gave the
session its organization was revoked. Forcing an organization onto those rows
would mean either inventing one or not recording the event.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import TimestampTZ, uuid_pk


class AuditAction(str):
    """Namespaced audit action names.

    Plain string constants rather than an enum: audit actions accumulate with
    every feature, and a database CHECK or a Python enum that must be extended
    for each new event is friction that eventually gets bypassed by logging
    nothing.
    """

    USER_REGISTERED = "user.registered"
    ORGANIZATION_CREATED = "organization.created"
    SESSION_LOGIN_SUCCEEDED = "session.login_succeeded"
    SESSION_LOGIN_FAILED = "session.login_failed"
    SESSION_LOGGED_OUT = "session.logged_out"
    SESSION_REJECTED = "session.rejected"
    ORGANIZATION_SWITCHED = "organization.switched"
    #: Recorded even though the request is anonymous: a burst of these against
    #: addresses that do not exist is what account enumeration looks like.
    # The S105 suppressions below are false positives: these are audit action
    # names, not credentials. The rule fires on any constant whose name
    # contains "PASSWORD".
    PASSWORD_RESET_REQUESTED = "password.reset_requested"  # noqa: S105
    PASSWORD_RESET_COMPLETED = "password.reset_completed"  # noqa: S105


class AuditLog(Base):
    """One recorded event.

    Not tenant-scoped through :class:`~app.db.tenancy.OrganizationScoped`,
    because that mixin requires a NOT NULL organization_id. Reads of this table
    must still be scoped by the caller; the API only ever exposes it filtered to
    the active organization.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        # The two ways this table is read: an organization's activity feed, and
        # one actor's history. DESC because both are "most recent first".
        Index(
            "ix_audit_logs_organization_id_created_at",
            "organization_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_audit_logs_actor_user_id_created_at",
            "actor_user_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    #: Nullable by design — see the module docstring. SET NULL rather than
    #: CASCADE: deleting an organization must not erase the record that it
    #: existed and what was done in it.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Null for events with no authenticated actor, such as a failed login.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    action: Mapped[str] = mapped_column(String(64), nullable=False)

    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: Event-specific context. Never credentials, never tokens — writes go
    #: through app.services.audit, which redacts before persisting.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Ties an audit row to the API log lines for the same request.
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TimestampTZ, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<AuditLog action={self.action} organization_id={self.organization_id}>"
