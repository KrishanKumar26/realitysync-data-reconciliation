"""Session — server-side authentication state.

Sessions are opaque and server-side rather than self-contained tokens (JWTs).
The deciding property is revocation: an operator must be able to end a session
immediately — on logout, on password change, on "sign out everywhere", on
suspected compromise. A stateless token stays valid until it expires no matter
what the server thinks, and every workaround for that (short TTLs plus refresh
tokens, deny-lists) reintroduces the server-side state it was meant to avoid,
with more moving parts.

The cookie carries a random 256-bit token. Only its SHA-256 hash is stored, so
a database disclosure does not hand over usable sessions.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import TimestampTZ, uuid_pk

if TYPE_CHECKING:
    from app.models.user import User


class Session(Base):
    """An authenticated session belonging to one user."""

    __tablename__ = "sessions"
    __table_args__ = (
        # The load-bearing constraint of the whole tenancy model.
        #
        # A session's active organization must be one the user is actually a
        # member of, and PostgreSQL enforces it: (user_id, active_organization_id)
        # must exist in memberships(user_id, organization_id). No application
        # bug can point a session at an organization the user cannot access.
        #
        # active_organization_id is nullable, and under the default MATCH SIMPLE
        # semantics a NULL in the composite key skips the check entirely — which
        # is exactly right for a session that has not selected an organization.
        #
        # ON DELETE CASCADE means revoking someone's membership destroys the
        # sessions they were using in that organization. Access ends when the
        # membership ends, not whenever the cookie happens to expire.
        ForeignKeyConstraint(
            ["user_id", "active_organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name="fk_sessions_active_membership",
            ondelete="CASCADE",
        ),
        # Session cleanup and "sign out everywhere" both scan live sessions.
        # Partial index: revoked rows are never the target of those queries.
        Index(
            "ix_sessions_user_id_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_sessions_expires_at_active",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: SHA-256 of the cookie token, hex-encoded. A fast hash is the correct
    #: choice here and not a weakness: the token is 256 bits of CSPRNG output,
    #: so there is no guessable input to attack — unlike a password, which is
    #: why passwords get Argon2id and this does not. Every authenticated
    #: request looks a session up by this value, so it must also be fast.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    #: Paired CSRF token, echoed by the browser in a request header. Stored so
    #: the server validates against its own record rather than trusting that
    #: two attacker-influenced cookie values happen to match.
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The organization this session is currently acting in. Null when the user
    #: has no memberships, or before one is selected.
    active_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )

    issued_at: Mapped[datetime] = mapped_column(
        TimestampTZ, nullable=False, server_default=func.now()
    )
    #: Absolute expiry. Reached regardless of activity.
    expires_at: Mapped[datetime] = mapped_column(TimestampTZ, nullable=False)
    #: Drives the idle timeout. Written at most once per touch interval so an
    #: active session does not cause a database write on every request.
    last_seen_at: Mapped[datetime] = mapped_column(
        TimestampTZ, nullable=False, server_default=func.now()
    )
    #: Set on logout or forced revocation. Rows are kept rather than deleted so
    #: "when did this session end, and why" remains answerable.
    revoked_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions", lazy="raise")

    def __repr__(self) -> str:
        # Never includes token_hash or csrf_token.
        return f"<Session id={self.id} user_id={self.user_id}>"
