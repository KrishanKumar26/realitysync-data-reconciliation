"""Entity, reality, conflict and timeline API models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

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
    """One observation's contribution. The provenance trail.

    Carries enough to answer "why does the system believe that" without a
    second request: which observation, from which source, what it said, when it
    was true, when we learned it, and what part it played. Anything less makes
    the caller join it back together themselves, and a provenance trail that
    needs assembling is one nobody checks.
    """

    observation_id: uuid.UUID
    #: Which source said it. The identity, not the credential.
    source_id: uuid.UUID
    stream_id: uuid.UUID
    external_id: str
    role: str
    #: Zero for every unscored state, meaning "not weighted" rather than
    #: "weighed and found worthless". The state's null confidence carries that
    #: distinction; this field cannot.
    weight: Decimal
    observed_value: Any = None
    #: When the source says it was true.
    event_time: datetime
    #: When RealitySync learned it. Separate on purpose — the two answer
    #: different questions and conflating them erases late arrival.
    ingested_at: datetime
    exclusion_reason: str | None = None


class RealityStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_id: uuid.UUID
    attribute: str
    #: None when no value was selected. ``value_selected`` distinguishes that
    #: from a source genuinely asserting JSON null.
    value: Any
    value_selected: bool
    #: None while the scoring specification is unavailable. Never 0 — a client
    #: must render "unavailable", not "0%".
    confidence: Decimal | None
    status: RealityStatus
    #: Every input to the score when one exists; otherwise the reason there is
    #: none, including which specifications are outstanding.
    confidence_breakdown: dict[str, Any]
    selection_reason: str
    valid_from: datetime
    valid_to: datetime | None
    calculated_at: datetime
    algorithm_version: str
    supporting_count: int
    dissenting_count: int
    source_count: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence_available(self) -> bool:
        """Explicit flag so a client branches on intent, not on a null.

        A missing field and a null field are easy to conflate with a
        serialisation bug; a boolean that says "no score exists" is not.
        """
        return self.confidence is not None


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
    #: How many written states carry no confidence score. States are written
    #: either way now — Phase 5 wrote none when scoring was blocked, which left
    #: the Reality page indistinguishable from "no data".
    states_unscored: int = 0
    #: Which attributes those are, and what each is blocked on.
    unscored_attributes: list[dict[str, str]] = Field(default_factory=list)
    #: True when no state could be scored. Kept from Phase 5 so existing
    #: clients continue to work; it no longer means "nothing was written".
    blocked: bool = False
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


class HistoricalAttributeResponse(BaseModel):
    """One field as it stood at a chosen moment."""

    model_config = ConfigDict(from_attributes=True)

    attribute: str
    status: str
    value: Any
    value_selected: bool
    confidence: Decimal | None
    confidence_available: bool
    selection_reason: str
    supporting_count: int
    dissenting_count: int
    source_count: int
    candidate_count: int


class RealityAsOfResponse(BaseModel):
    """What RealitySync would have said at ``known_at``.

    ``observations_since`` is the interesting number: it counts records that
    exist now but had not arrived by then. When a past answer differs from
    today's and no source changed its mind, this is why.
    """

    model_config = ConfigDict(from_attributes=True)

    entity_id: uuid.UUID
    known_at: datetime
    observations_known: int
    observations_since: int
    attributes: list[HistoricalAttributeResponse]
