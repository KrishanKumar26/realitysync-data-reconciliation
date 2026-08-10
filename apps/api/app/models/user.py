"""User — a global identity.

A user is not owned by an organization. People belong to more than one
workspace, change employers, and are invited to a customer's organization while
already having their own. Scoping identity to a tenant would make the same human
two unrelated accounts, and would make "switch organization" impossible without
logging out.

Tenancy lives on :class:`~app.models.membership.Membership`, not here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, String, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import TimestampMixin, TimestampTZ, uuid_pk

if TYPE_CHECKING:
    from app.models.membership import Membership
    from app.models.session import Session


class User(Base, TimestampMixin):
    """A person who can authenticate."""

    __tablename__ = "users"
    __table_args__ = (
        # Cheap structural sanity check at the storage layer. Real validation
        # happens in the Pydantic schema; this stops a repair script or a
        # future code path from writing something that is obviously not an
        # address.
        CheckConstraint(
            "position('@' in email) > 1 AND length(email) <= 320",
            name="email_shape",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    #: CITEXT, so uniqueness is case-insensitive in the database rather than
    #: depending on every insert path remembering to lower-case first.
    #: "Ada@example.com" and "ada@example.com" are one account, enforced by the
    #: unique index itself.
    email: Mapped[str] = mapped_column(CITEXT(), nullable=False, unique=True)

    #: Argon2id PHC string. Never serialised — see app/schemas/auth.py, which
    #: has no field for it, and the test that asserts it never appears in a
    #: response body.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)

    #: Soft disable. Deleting a user would take their audit trail with it, so
    #: deactivation is the operational lever: existing sessions stop resolving
    #: and new logins are refused.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    #: Null until verified. Email verification is not part of the MVP account
    #: lifecycle, but the column exists so enabling it later is a policy change
    #: rather than a migration during an incident.
    email_verified_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)

    last_login_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        # Deliberately excludes password_hash. Repr output reaches debuggers,
        # tracebacks and log lines.
        return f"<User id={self.id} email={self.email}>"
