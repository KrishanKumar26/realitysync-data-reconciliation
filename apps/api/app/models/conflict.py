"""Conflicts — recorded disagreement between sources.

A conflict is an observation *about* the evidence, not a competing belief. It
never changes the reality state: the engine selects a value by the approved
rules and separately records that the selection was contested. Letting conflict
resolution write back into the state would make the state depend on the order
in which conflicts were processed, and it would stop being reproducible from
observations alone.

So the relationship is one-way. The engine produces both; conflicts reference
the state; nothing reads a conflict to decide a value.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
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


class ConflictType(enum.StrEnum):
    """What kind of disagreement this is.

    ``VALUE_CONFLICT``
        Two or more sources assert different values for the same attribute at
        overlapping times. The core case.

    ``SOURCE_DISAGREEMENT``
        Sources disagree persistently across several attributes or repeated
        observations — a pattern, not a single divergence. Points at a
        systemic problem (a broken integration, a clock skew) rather than one
        bad row.

    ``CONTESTED_STATE``
        The winning value's margin over the runner-up is thin enough that the
        selection is not meaningfully decisive. Recorded because a 0.78%
        margin presented as a settled answer would be misleading.
    """

    VALUE_CONFLICT = "value_conflict"
    SOURCE_DISAGREEMENT = "source_disagreement"
    CONTESTED_STATE = "contested_state"


class ConflictSeverity(enum.StrEnum):
    """How much the disagreement matters. Derived from the conflict score."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConflictStatus(enum.StrEnum):
    """Lifecycle. Resolution is a human act and is recorded, not inferred."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    #: The disagreement no longer appears in the evidence.
    RESOLVED = "resolved"
    #: A human judged it not worth acting on.
    DISMISSED = "dismissed"


CONFLICT_TYPES: tuple[str, ...] = tuple(t.value for t in ConflictType)
#: Recorded when the severity thresholds are unspecified. A real level here
#: would be a guess presented as a grading.
UNSPECIFIED_SEVERITY = "unspecified"

CONFLICT_SEVERITIES: tuple[str, ...] = (
    *(s.value for s in ConflictSeverity),
    UNSPECIFIED_SEVERITY,
)
CONFLICT_STATUSES: tuple[str, ...] = tuple(s.value for s in ConflictStatus)


class Conflict(Base, OrganizationScoped):
    """One detected disagreement about one attribute of one entity."""

    __tablename__ = "conflicts"
    __table_args__ = (
        # One open conflict per (entity, attribute, type). Re-running the
        # engine updates the existing row rather than accumulating a duplicate
        # on every calculation — the fingerprint below makes "same conflict"
        # a deterministic question.
        UniqueConstraint(
            "entity_id",
            "attribute",
            "conflict_type",
            "fingerprint",
            name="uq_conflicts_entity_attribute_type_fingerprint",
        ),
        CheckConstraint(
            "conflict_type IN ('" + "', '".join(CONFLICT_TYPES) + "')",
            name="conflict_type_valid",
        ),
        CheckConstraint(
            "severity IN ('" + "', '".join(CONFLICT_SEVERITIES) + "')",
            name="severity_valid",
        ),
        CheckConstraint(
            "status IN ('" + "', '".join(CONFLICT_STATUSES) + "')", name="status_valid"
        ),
        # Nullable: a conflict can be detected before it can be graded, and a
        # NULL score is an honest "not scored" rather than a misleading 0.
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="score_range"),
        Index(
            "ix_conflicts_organization_detected_at",
            "organization_id",
            text("detected_at DESC"),
        ),
        Index("ix_conflicts_organization_status_severity", "organization_id", "status", "severity"),
        Index("ix_conflicts_entity_id_attribute", "entity_id", "attribute"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = organization_id_column(index=False)

    entity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The state this conflict was detected alongside. SET NULL rather than
    #: CASCADE: recalculation replaces states, and the conflict record should
    #: survive that so its history is not silently erased.
    reality_state_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reality_states.id", ondelete="SET NULL"),
        nullable=True,
    )

    attribute: Mapped[str] = mapped_column(String(128), nullable=False)
    conflict_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))

    #: 0-1. Deterministic function of the competing weights and the size of
    #: the divergence. NULL while the conflict-score formula is unspecified —
    #: detection does not depend on grading, so the conflict is still recorded.
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)

    #: Deterministic identity of *this* disagreement — the competing values and
    #: the sources asserting them. Re-running the engine on unchanged evidence
    #: produces the same fingerprint and updates in place; a genuinely
    #: different disagreement produces a new row.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The competing values, their weights, which sources asserted each, the
    #: divergence and the margin. Frozen at detection so the record stays
    #: meaningful after the underlying observations have moved on.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    summary: Mapped[str] = mapped_column(Text, nullable=False)

    detected_at: Mapped[datetime] = mapped_column(
        TimestampTZ, nullable=False, server_default=func.now()
    )
    #: Bumped each time the engine still sees this conflict, so "how long has
    #: this been true" is answerable without a separate event log.
    last_seen_at: Mapped[datetime] = mapped_column(
        TimestampTZ, nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Conflict {self.conflict_type} {self.severity} score={self.score} {self.attribute}>"
        )
