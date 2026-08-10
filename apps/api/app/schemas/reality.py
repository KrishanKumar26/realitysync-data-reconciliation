"""Entity, reality, conflict and timeline API models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.conflict import ConflictStatus
from app.models.reality_state import RealityStatus

Identifier = Annotated[str, Field(min_length=1, max_length=256)]


# --- Entities --------------------------------------------------------------


class CreateEntityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: Annotated[str, Field(min_length=1, max_length=64)]
    natural_key: Identifier
    display_name: Annotated[str, Field(max_length=256)] | None = None

    @field_validator("entity_type", "natural_key")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Value must not be blank")
        return stripped


class CreateMappingRequest(BaseModel):
    """Declare that a source row describes this entity.

    Deliberately explicit: MVP entity resolution is a human decision, and this
    request is that decision being recorded.
    """

    model_config = ConfigDict(extra="forbid")

    stream_id: uuid.UUID
    external_id: Annotated[str, Field(min_length=1, max_length=512)]


class EntityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str
    natural_key: str
    display_name: str | None
    mapping_count: int = 0
    observation_count: int = 0
    created_at: datetime


class MappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_id: uuid.UUID
    stream_id: uuid.UUID
    external_id: str
    created_at: datetime


# --- Reality state ---------------------------------------------------------


class EvidenceResponse(BaseModel):
    """One observation's contribution. The provenance trail."""

    observation_id: uuid.UUID
    role: str
    weight: Decimal
    observed_value: Any = None
    exclusion_reason: str | None = None


class RealityStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_id: uuid.UUID
    attribute: str
    value: Any
    confidence: Decimal
    status: RealityStatus
    #: Every input to the score, so it can be rechecked by hand.
    confidence_breakdown: dict[str, Any]
    selection_reason: str
    valid_from: datetime
    valid_to: datetime | None
    calculated_at: datetime
    algorithm_version: str
    supporting_count: int
    dissenting_count: int
    source_count: int


class UnscoredAttributeResponse(BaseModel):
    """What is known about an attribute when scoring is unavailable.

    Not a reality state and deliberately not shaped like one: no value, no
    confidence, no status. It reports which distinct values the sources assert
    and whether they disagree — facts that need no formula — and nothing more.
    """

    attribute: str
    scored: Literal[False] = False
    disagreement: bool
    divergence: str | None = None
    distinct_values: list[dict[str, Any]] = Field(default_factory=list)
    excluded: list[dict[str, Any]] = Field(default_factory=list)


class RecalculateResponse(BaseModel):
    """Outcome of a recalculation run."""

    entity_id: uuid.UUID
    attributes_considered: int
    states_written: int
    conflicts_written: int
    calculated_at: datetime
    #: True when nothing could be scored. The interface must say so rather
    #: than showing an empty Reality page that looks like "no data".
    blocked: bool
    blocked_on: list[str] = Field(default_factory=list)
    missing_specifications: list[dict[str, str]] = Field(default_factory=list)


# --- Conflicts -------------------------------------------------------------


class ConflictResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_id: uuid.UUID
    entity_natural_key: str | None = None
    reality_state_id: uuid.UUID | None
    attribute: str
    conflict_type: str
    #: "unspecified" while the severity thresholds are missing. Not graded as
    #: low, which would read as "harmless".
    severity: str
    status: ConflictStatus
    #: NULL while the conflict-score formula is missing.
    score: Decimal | None
    summary: str
    details: dict[str, Any]
    detected_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None


class UpdateConflictRequest(BaseModel):
    """Move a conflict through its lifecycle.

    Resolution is a human act. The engine never sets these — it reports what it
    sees, and a person decides what to do about it.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["acknowledged", "resolved", "dismissed"]
    note: Annotated[str, Field(max_length=2000)] | None = None


# --- Timeline --------------------------------------------------------------


class TimelineEventResponse(BaseModel):
    observation_id: uuid.UUID
    external_id: str
    source_id: uuid.UUID
    source_name: str
    values: dict[str, Any]
    #: When the fact was true, per the source.
    event_time: datetime
    #: When RealitySync learned it.
    ingested_at: datetime
    event_time_semantics: str
    #: True when learned after it was true — the signal that the two axes have
    #: diverged for this record.
    arrived_late: bool
    lag_seconds: float


class TimelineResponse(BaseModel):
    """A bitemporal reconstruction, with the parameters that produced it.

    The parameters are returned because a timeline read without knowing which
    axis produced it is uninterpretable — two views of the same entity can
    legitimately differ.
    """

    axis: Literal["event", "knowledge"]
    as_of_event_time: datetime | None
    as_of_knowledge_time: datetime | None
    event_count: int
    late_arrival_count: int
    truncated: bool
    events: list[TimelineEventResponse]
