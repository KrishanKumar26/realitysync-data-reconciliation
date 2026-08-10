"""Entities — the things RealitySync holds beliefs about.

An observation says *a source stated this about this row*. A reality state says
*this is what we believe about this thing*. The gap between the two is entity
resolution: deciding that `shipments.shipment_id=1` in the warehouse and
`orders.ref=REF-001` in the ERP are the same real-world thing.

MVP resolution is **deterministic and manual**, as settled in Phase 0. A person
declares the mapping; nothing infers it. An inferred identity that turns out
wrong merges two real things irreversibly — every downstream state, conflict
and explanation would then be about a chimera, and no later correction could
untangle which observation belonged to which.

So there is no matching algorithm here. There is a mapping table, and it is
populated by explicit human decision.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenancy import OrganizationScoped, organization_id_column
from app.db.types import TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from app.models.source_stream import SourceStream


class Entity(Base, OrganizationScoped, TimestampMixin):
    """One real-world thing, within one organization."""

    __tablename__ = "entities"
    __table_args__ = (
        # The natural key is how a human refers to this thing — "LAPTOP-001".
        # Unique per (organization, type) so two tenants can both have an
        # asset called LAPTOP-001 without collision.
        UniqueConstraint(
            "organization_id",
            "entity_type",
            "natural_key",
            name="uq_entities_organization_type_natural_key",
        ),
        CheckConstraint("length(btrim(natural_key)) > 0", name="natural_key_not_blank"),
        CheckConstraint("length(btrim(entity_type)) > 0", name="entity_type_not_blank"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    #: Not separately indexed: uq_entities_organization_type_natural_key
    #: already leads with organization_id.
    organization_id: Mapped[uuid.UUID] = organization_id_column(index=False)

    #: What kind of thing: "asset", "order", "shipment". Free text rather than
    #: an enum — the taxonomy belongs to the customer's domain, not to us.
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The customer's own identifier for it.
    natural_key: Mapped[str] = mapped_column(String(256), nullable=False)

    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    mappings: Mapped[list[EntityMapping]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
        lazy="raise",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Entity id={self.id} {self.entity_type}:{self.natural_key}>"


class EntityMapping(Base, OrganizationScoped, TimestampMixin):
    """A declared link from one source row to one entity.

    ``(stream_id, external_id) -> entity_id``. Created deliberately, never
    inferred. The observation table's ``external_id`` is the join key, so
    mapping a stream retroactively resolves every observation it has already
    produced — no re-sync required, and no observation is ever rewritten.
    """

    __tablename__ = "entity_mappings"
    __table_args__ = (
        # One source row maps to at most one entity. Two mappings for the same
        # row would make "which entity does this observation support?"
        # ambiguous, and the engine would have no non-arbitrary answer.
        UniqueConstraint("stream_id", "external_id", name="uq_entity_mappings_stream_external_id"),
        CheckConstraint("length(btrim(external_id)) > 0", name="external_id_not_blank"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = organization_id_column()

    entity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stream_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_streams.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Matches Observation.external_id exactly.
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)

    #: Recorded because a mapping is a human assertion, and "who decided this
    #: and when" is part of the evidence trail for every state built on it.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    entity: Mapped[Entity] = relationship(back_populates="mappings", lazy="raise")
    stream: Mapped[SourceStream] = relationship(lazy="raise")

    def __repr__(self) -> str:
        return f"<EntityMapping {self.external_id} -> {self.entity_id}>"
