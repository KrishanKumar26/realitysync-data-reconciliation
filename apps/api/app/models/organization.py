"""Organization — the tenant boundary.

Every organization-owned record in RealitySync carries this table's id. It is
the unit of data isolation, billing and access control.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from app.models.membership import Membership


class Organization(Base, TimestampMixin):
    """A tenant workspace."""

    __tablename__ = "organizations"
    __table_args__ = (
        # Slugs appear in URLs. Constrain the shape in the database so a
        # malformed slug cannot be introduced by any write path: lower-case
        # alphanumerics and single interior hyphens.
        CheckConstraint(
            r"slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name="slug_format",
        ),
        CheckConstraint("length(slug) BETWEEN 2 AND 64", name="slug_length"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    #: CITEXT for the same reason as User.email: case-insensitive uniqueness
    #: belongs in the index, not in a convention.
    slug: Mapped[str] = mapped_column(CITEXT(), nullable=False, unique=True)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id} slug={self.slug}>"
