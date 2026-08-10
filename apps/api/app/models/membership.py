"""Membership — the edge connecting a user to an organization.

This table *is* the tenancy model. A user has access to an organization if and
only if a membership row exists, and the role on that row decides what they may
do there. There is no other path to organization data.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenancy import OrganizationScoped
from app.db.types import TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User


class OrganizationRole(enum.StrEnum):
    """Role within an organization.

    Ordered by privilege. Stored as text with a CHECK constraint rather than a
    PostgreSQL ENUM type: adding a role to a native enum requires an
    ``ALTER TYPE`` that cannot run inside a transaction on older servers, while
    a CHECK constraint is a single ordinary migration. The database still
    rejects unknown values either way.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        """Higher rank means more privilege. Used for `>=` authorization checks."""
        return _ROLE_RANK[self]

    def satisfies(self, required: OrganizationRole) -> bool:
        """True when this role is at least as privileged as `required`."""
        return self.rank >= required.rank


_ROLE_RANK: dict[OrganizationRole, int] = {
    OrganizationRole.VIEWER: 0,
    OrganizationRole.MEMBER: 1,
    OrganizationRole.ADMIN: 2,
    OrganizationRole.OWNER: 3,
}

#: Literal values accepted by the database CHECK constraint.
ROLE_VALUES: tuple[str, ...] = tuple(role.value for role in OrganizationRole)


class Membership(Base, OrganizationScoped, TimestampMixin):
    """A user's role in one organization."""

    __tablename__ = "memberships"
    __table_args__ = (
        # One membership per (user, organization). This is also the key the
        # sessions composite foreign key points at, which is what makes
        # "a session's active organization must be one the user belongs to"
        # a database guarantee rather than an application convention.
        UniqueConstraint("user_id", "organization_id", name="uq_memberships_user_organization"),
        CheckConstraint(
            "role IN ('" + "', '".join(ROLE_VALUES) + "')",
            name="role_valid",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    #: Not separately indexed: the unique constraint above already creates a
    #: btree on (user_id, organization_id), and PostgreSQL uses its leftmost
    #: prefix for user_id-only lookups such as "which organizations am I in".
    #: A dedicated index would be a second copy of the same data to maintain.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # organization_id comes from OrganizationScoped: NOT NULL, indexed,
    # ON DELETE CASCADE, and registered with the tenancy guard.

    role: Mapped[str] = mapped_column(String(32), nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships", lazy="raise")
    organization: Mapped[Organization] = relationship(back_populates="memberships", lazy="raise")

    @property
    def role_enum(self) -> OrganizationRole:
        return OrganizationRole(self.role)

    def __repr__(self) -> str:
        return (
            f"<Membership user_id={self.user_id} "
            f"organization_id={self.organization_id} role={self.role}>"
        )
