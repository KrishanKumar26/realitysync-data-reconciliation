"""Reality state — what RealitySync currently believes, and why.

One row per ``(entity, attribute)``: the current believed value, its
confidence, the status, and enough structure to answer every question in the
Phase 4 brief without consulting anything else:

============================================  ==============================
What is the current believed value?           ``value``
Why was this value selected?                  ``selection_reason`` + evidence
Which observations contributed?               evidence rows, SUPPORTING
Which observations disagreed?                 evidence rows, DISSENTING
What is the confidence?                       ``confidence``
What is the status?                           ``status``
When was the state valid?                     ``valid_from`` / ``valid_to``
When was it calculated?                       ``calculated_at``
Which algorithm produced it?                  ``algorithm_version``
============================================  ==============================

``confidence_breakdown`` stores every input to the score — the ceiling, each
weighted factor, each penalty. That is what makes a number re-derivable rather
than merely asserted: given the breakdown, anyone can recompute the score by
hand and get the same answer.

Reality state is **derived**, never authoritative. It can be deleted and
recomputed from observations at any time and must come out identical. Nothing
downstream may write to it except the engine.
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.tenancy import OrganizationScoped, organization_id_column
from app.db.types import TimestampTZ, uuid_pk


class RealityStatus(enum.StrEnum):
    """How settled the belief is.

    ``CONFIRMED``
        Sources agree, or only one source speaks and it is reliable.

    ``CONTESTED``
        Sources disagree materially. A value is still selected — refusing to
        answer would be less useful than answering with the disagreement
        stated — but the interface must show it as contested, not as fact.

    ``PROVISIONAL``
        Thin evidence: a single low-reliability source, or everything stale.

    ``STALE``
        Nothing recent enough to be worth asserting.

    ``UNKNOWN``
        No usable observations. An honest absence, not a guess.
    """

    CONFIRMED = "confirmed"
    CONTESTED = "contested"
    PROVISIONAL = "provisional"
    STALE = "stale"
    UNKNOWN = "unknown"


class EvidenceRole(enum.StrEnum):
    """How an observation related to the selected value."""

    #: Agreed with the value that won.
    SUPPORTING = "supporting"
    #: Asserted a different value.
    DISSENTING = "dissenting"
    #: Considered but excluded — superseded by a newer observation from the
    #: same source, or failed validation. Recorded rather than dropped so the
    #: evidence trail shows what was looked at, not only what counted.
    EXCLUDED = "excluded"


REALITY_STATUSES: tuple[str, ...] = tuple(s.value for s in RealityStatus)
EVIDENCE_ROLES: tuple[str, ...] = tuple(r.value for r in EvidenceRole)


class RealityState(Base, OrganizationScoped):
    """The current believed value of one attribute of one entity."""

    __tablename__ = "reality_states"
    __table_args__ = (
        # One current state per (entity, attribute). History lives in the
        # observations and in superseded conflict records; this table is the
        # present tense.
        UniqueConstraint("entity_id", "attribute", name="uq_reality_states_entity_attribute"),
        CheckConstraint("status IN ('" + "', '".join(REALITY_STATUSES) + "')", name="status_valid"),
        # The approved bound. 100% would claim certainty, which no finite set
        # of observations can justify.
        CheckConstraint("confidence >= 0 AND confidence <= 99", name="confidence_range"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range_ordered"),
        CheckConstraint("length(btrim(attribute)) > 0", name="attribute_not_blank"),
        Index(
            "ix_reality_states_organization_calculated_at",
            "organization_id",
            text("calculated_at DESC"),
        ),
        Index("ix_reality_states_organization_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()

    #: Not separately indexed: two composite indexes above lead with it.
    organization_id: Mapped[uuid.UUID] = organization_id_column(index=False)

    entity_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: The attribute name as it appears in observation payloads.
    attribute: Mapped[str] = mapped_column(String(128), nullable=False)

    #: The believed value, in the canonical JSON form produced by
    #: app.ingestion.normalization — so a numeric keeps its scale and
    #: comparison is exact rather than float-approximate.
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)

    #: 0-99, one decimal place. NUMERIC not float: a confidence that renders
    #: as 71.0 in one place and 70.99999 in another is not reproducible, and
    #: reproducibility is the whole point.
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)

    #: Every input to the score: ceiling, each weighted factor with its weight
    #: and contribution, each penalty. Enough to recompute by hand.
    confidence_breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    #: Plain-language statement of why this value won, generated
    #: deterministically from the calculation. Not AI-written — the engine
    #: knows exactly why it chose, and a template renders it.
    selection_reason: Mapped[str] = mapped_column(Text, nullable=False)

    #: Valid time: when this value became true, per the sources.
    valid_from: Mapped[datetime] = mapped_column(TimestampTZ, nullable=False)
    #: Null means "still believed". Set when a later value supersedes it.
    valid_to: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)

    #: Transaction time: when the engine ran. Distinct from valid_from, and
    #: the pair is what makes "what did we believe at 10:30" answerable.
    calculated_at: Mapped[datetime] = mapped_column(
        TimestampTZ, nullable=False, server_default=func.now()
    )

    #: Bumping this invalidates every stored state, so a formula change is an
    #: explicit, traceable event rather than a silent drift in the numbers.
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)

    #: Counts, denormalised for listing screens that would otherwise need a
    #: join per row. Derived, never authoritative — the evidence rows are.
    supporting_count: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    dissenting_count: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    source_count: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))

    evidence: Mapped[list[RealityStateEvidence]] = relationship(
        back_populates="reality_state",
        cascade="all, delete-orphan",
        lazy="raise",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return (
            f"<RealityState entity={self.entity_id} {self.attribute}="
            f"{self.value!r} conf={self.confidence} {self.status}>"
        )


class RealityStateEvidence(Base, OrganizationScoped):
    """One observation's contribution to one reality state.

    The link that makes every assertion traceable. A reality state with no
    evidence rows is an unsupported claim, and the engine never writes one:
    ``UNKNOWN`` states exist precisely so that "we have nothing" is expressible
    without inventing a value.
    """

    __tablename__ = "reality_state_evidence"
    __table_args__ = (
        UniqueConstraint(
            "reality_state_id",
            "observation_id",
            name="uq_reality_state_evidence_state_observation",
        ),
        CheckConstraint("role IN ('" + "', '".join(EVIDENCE_ROLES) + "')", name="role_valid"),
        CheckConstraint("weight >= 0", name="weight_non_negative"),
        Index("ix_reality_state_evidence_observation_id", "observation_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = organization_id_column()

    reality_state_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reality_states.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    observation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("observations.id", ondelete="CASCADE"),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(String(16), nullable=False)

    #: The observation's computed weight (reliability x freshness x quality).
    #: Stored so the arithmetic behind the score can be checked per row.
    weight: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)

    #: The value this observation asserted, so the evidence list reads without
    #: joining back to the observation payload.
    observed_value: Mapped[Any] = mapped_column(JSONB, nullable=True)

    #: Why it was excluded, when role is EXCLUDED.
    exclusion_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    reality_state: Mapped[RealityState] = relationship(back_populates="evidence", lazy="raise")

    def __repr__(self) -> str:
        return f"<Evidence {self.role} weight={self.weight} obs={self.observation_id}>"
