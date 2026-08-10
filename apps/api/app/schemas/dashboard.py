"""Overview API models.

Every numeric field here is a real count from a real table. There is no field
for an estimated, projected or illustrative value, which is what keeps the
dashboard honest by construction: a fabricated metric would need a new field to
live in.

:class:`ConfidenceResponse` is the exception that proves the rule — its numbers
are optional precisely because the confidence specification is unavailable, and
``available: false`` is the honest answer rather than a zero.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.data_source import SourceStatus


class SourceHealthResponse(BaseModel):
    """One source's health, from its last real connection and sync."""

    source_id: uuid.UUID
    name: str
    kind: str
    status: SourceStatus
    stream_count: int
    observation_count: int
    last_connected_at: datetime | None
    last_synced_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    #: Credentials stored but never verified — distinct from unhealthy.
    never_tested: bool


class SourceSummaryResponse(BaseModel):
    total: int
    connected: int
    never_tested: int
    errored: int
    disabled: int
    sources: list[SourceHealthResponse] = Field(default_factory=list)


class IngestionSummaryResponse(BaseModel):
    observation_count: int
    observations_in_window: int
    entity_count: int
    mapped_entity_count: int
    unmapped_entity_count: int
    stream_count: int
    enabled_stream_count: int
    last_sync_at: datetime | None
    syncs_in_window: int
    failed_syncs_in_window: int


class ConflictSummaryResponse(BaseModel):
    open: int
    acknowledged: int
    resolved: int
    dismissed: int
    outstanding: int
    #: Graded buckets only.
    by_severity: dict[str, int] = Field(default_factory=dict)
    #: Detected but not assessed. Reported apart from the graded buckets so an
    #: absent judgement is never displayed as a mild one.
    ungraded: int = 0
    newest_open_at: datetime | None = None


class MissingSpecification(BaseModel):
    name: str
    description: str


class ConfidenceResponse(BaseModel):
    """Reality confidence, or an explicit statement that it is unavailable.

    When ``available`` is false every number is null rather than zero. A zero
    would render as a gauge reading "no confidence", which is a claim about the
    data; the truth is that nobody has told us how to measure.
    """

    available: bool
    scored_state_count: int
    unscored_attribute_count: int
    average_confidence: float | None = None
    lowest_confidence: float | None = None
    highest_confidence: float | None = None
    algorithm_version: str
    blocked_reason: str | None = None
    missing_specifications: list[MissingSpecification] = Field(default_factory=list)


class ActivityItemResponse(BaseModel):
    kind: str
    occurred_at: datetime
    summary: str
    detail: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    severity: str | None = None


class DashboardResponse(BaseModel):
    """Everything the Overview renders."""

    organization_id: uuid.UUID
    generated_at: datetime
    window_days: int
    #: True when nothing is connected yet — the onboarding state, distinct
    #: from "connected but quiet", which shows real zeroes.
    is_empty: bool
    sources: SourceSummaryResponse
    ingestion: IngestionSummaryResponse
    conflicts: ConflictSummaryResponse
    confidence: ConfidenceResponse
    activity: list[ActivityItemResponse] = Field(default_factory=list)
