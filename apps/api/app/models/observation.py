"""Observations — the atom of RealitySync.

An observation is one immutable statement: *this source said this about this
thing at this time*. Everything downstream — reality state, confidence,
conflicts, the timeline — is a function of observations. They are append-only,
never updated and never deleted by the ingestion path.

Two independent time axes, both required:

``event_time``
    When the fact was true, according to the source. Never overwritten with
    ingestion time — that substitution is the single most destructive thing a
    pipeline can do, because it makes a late correction indistinguishable from
    a fresh change.

``ingested_at``
    When RealitySync learned it.

Out-of-order arrival is normal and needs no special handling: an observation
inserted today may carry an event time from last week, and the ordering that
matters is whichever axis the question is about.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenancy import OrganizationScoped, organization_id_column
from app.db.types import TimestampTZ, uuid_pk


class EntityMappingState(enum.StrEnum):
    """Whether this observation's subject has been resolved to an entity.

    MVP entity resolution is deterministic and manual, so most observations
    start ``unmapped``: RealitySync knows the source's identifier for the row
    and nothing more. That is recorded honestly rather than guessed at — an
    invented identity would silently merge two real-world things, and no
    downstream reconciliation could recover from it.
    """

    UNMAPPED = "unmapped"
    MAPPED = "mapped"
    AMBIGUOUS = "ambiguous"


MAPPING_STATES: tuple[str, ...] = tuple(s.value for s in EntityMappingState)


class Observation(Base, OrganizationScoped):
    """One immutable statement from one source.

    No ``updated_at``: an observation is never modified. A correction is a new
    observation with a later ``ingested_at``, which is what preserves the
    history of what was believed and when.
    """

    __tablename__ = "observations"
    __table_args__ = (
        # Idempotency, enforced by the database rather than by the sync code
        # checking first. Re-reading an unchanged row produces the same
        # fingerprint, and the insert is a no-op. Scoped per stream so two
        # streams over similar tables cannot collide.
        UniqueConstraint("stream_id", "fingerprint", name="uq_observations_stream_fingerprint"),
        CheckConstraint(
            "entity_mapping_state IN ('" + "', '".join(MAPPING_STATES) + "')",
            name="entity_mapping_state_valid",
        ),
        CheckConstraint("length(fingerprint) = 64", name="fingerprint_length"),
        CheckConstraint("length(btrim(external_id)) > 0", name="external_id_not_blank"),
        # "What did this source say about this thing, most recently first" —
        # the query behind an entity's evidence list.
        Index(
            "ix_observations_stream_external_event_time",
            "stream_id",
            "external_id",
            text("event_time DESC"),
        ),
        # "What arrived since I last looked" — ingestion-time ordering, which
        # is a different question from event-time ordering and needs its own
        # index precisely because the two disagree for late arrivals.
        Index(
            "ix_observations_organization_ingested_at",
            "organization_id",
            text("ingested_at DESC"),
        ),
        Index("ix_observations_source_id_event_time", "source_id", text("event_time DESC")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    #: Not separately indexed: ix_observations_organization_ingested_at leads
    #: with organization_id, and this is the highest-volume table in the
    #: system — a duplicate index would be maintained on every single insert.
    organization_id: Mapped[uuid.UUID] = organization_id_column(index=False)

    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    stream_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_streams.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: The source's own identifier for the row, built from the stream's primary
    #: key columns. Preserved verbatim so provenance stays traceable back to a
    #: specific row in a specific table.
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)

    #: Resolved entity, once mapping exists. Null while unmapped — the
    #: observation is kept regardless, because discarding a fact for lack of a
    #: mapping loses data that cannot be recovered later.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    entity_mapping_state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'unmapped'")
    )

    #: The normalised row. Values are canonicalised to JSON-safe forms
    #: (numeric to string, timestamps to ISO-8601 UTC) so the same database row
    #: always produces the same bytes, which is what makes fingerprints stable.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    #: When the fact was true, per the source.
    event_time: Mapped[datetime] = mapped_column(TimestampTZ, nullable=False)
    #: What event_time actually means — copied from the stream at ingestion so
    #: an observation stays interpretable even if the stream is reconfigured.
    event_time_semantics: Mapped[str] = mapped_column(String(32), nullable=False)

    #: When RealitySync learned it.
    ingested_at: Mapped[datetime] = mapped_column(
        TimestampTZ, nullable=False, server_default=func.now()
    )

    #: SHA-256 over the identity-bearing parts of this observation. Two reads
    #: of an unchanged row produce the same value; any change produces a
    #: different one.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Where this came from: schema, table, primary key columns, the sync run
    #: that produced it, connector version. Enough to answer "why do you
    #: believe this" without another system.
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    def __repr__(self) -> str:
        return (
            f"<Observation id={self.id} external_id={self.external_id} "
            f"event_time={self.event_time.isoformat()}>"
        )
