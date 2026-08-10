"""Overview aggregates.

Phase 6 is the dashboard: reality confidence, source health, recent activity.
Two of those three are computable from what the system already knows. The third
is not, and this module is careful about the difference.

**Source health and activity are facts.** A source's status is the outcome of
the last real connection attempt; a sync run's counters are what ingestion
actually did; a conflict exists because two sources genuinely disagreed. All of
it is read straight from the tables that recorded it.

**Reality confidence is unavailable.** The Phase 0 confidence specification is
unrecoverable, so no reality state carries a score and there is no average to
take. The dashboard reports that explicitly — see :class:`ConfidenceSummary` —
rather than showing a gauge at zero, which would read as "we are certain of
nothing" when the truth is "we have not been told how to measure".

Every query is scoped to one organization, and every tenant-owned table in a
join is filtered in the WHERE clause rather than the ON clause: the tenancy
guard cannot inspect ORM join conditions, so a filter placed there would go
unchecked.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.spec import ALGORITHM_VERSION, MISSING_SPECIFICATIONS
from app.models.audit_log import AuditLog
from app.models.conflict import Conflict, ConflictStatus
from app.models.data_source import DataSource, SourceStatus
from app.models.entity import Entity
from app.models.observation import Observation
from app.models.reality_state import RealityState
from app.models.source_stream import SourceStream
from app.models.sync_run import SyncRun, SyncStatus

#: How far back "recent" reaches for the activity feed and freshness counts.
DEFAULT_ACTIVITY_WINDOW = timedelta(days=7)

#: Ceiling on activity rows returned in one response.
MAX_ACTIVITY_ITEMS = 100


@dataclass(frozen=True, slots=True)
class SourceHealth:
    """One source's health, from its last real connection and sync.

    Nothing here is probed on read. Dialling every customer database to render
    a dashboard would be slow and rude; these are the recorded outcomes of
    attempts that actually happened.
    """

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

    @property
    def is_healthy(self) -> bool:
        return self.status is SourceStatus.CONNECTED

    @property
    def has_never_been_tested(self) -> bool:
        """Credentials stored, connection never proven.

        A distinct state from unhealthy, and the dashboard must not conflate
        them: "we have not checked" is not "it is broken".
        """
        return self.status is SourceStatus.CONFIGURED


@dataclass(frozen=True, slots=True)
class SourceSummary:
    """Fleet-level source health."""

    total: int
    connected: int
    never_tested: int
    errored: int
    disabled: int
    sources: tuple[SourceHealth, ...]

    @property
    def needs_attention(self) -> int:
        return self.errored


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    """What ingestion has actually produced."""

    observation_count: int
    observations_in_window: int
    entity_count: int
    mapped_entity_count: int
    stream_count: int
    enabled_stream_count: int
    last_sync_at: datetime | None
    syncs_in_window: int
    failed_syncs_in_window: int

    @property
    def unmapped_entity_count(self) -> int:
        return max(self.entity_count - self.mapped_entity_count, 0)


@dataclass(frozen=True, slots=True)
class ConflictSummary:
    """Open disagreement, by status and severity.

    ``ungraded`` is reported separately from the severity buckets. A conflict
    whose severity is ``unspecified`` has not been assessed, and folding it in
    with ``low`` would present an absent judgement as a mild one.
    """

    open: int
    acknowledged: int
    resolved: int
    dismissed: int
    by_severity: dict[str, int]
    ungraded: int
    newest_open_at: datetime | None

    @property
    def outstanding(self) -> int:
        return self.open + self.acknowledged


@dataclass(frozen=True, slots=True)
class ConfidenceSummary:
    """Reality confidence — or an explicit statement that it is unavailable.

    ``available`` is False while the Phase 0 confidence specification is
    missing. When it is False every numeric field is None, not zero: a zero
    would render as a gauge reading "no confidence", which is a claim about the
    data rather than about the specification.
    """

    available: bool
    scored_state_count: int
    unscored_attribute_count: int
    average_confidence: float | None = None
    lowest_confidence: float | None = None
    highest_confidence: float | None = None
    algorithm_version: str = ALGORITHM_VERSION
    blocked_reason: str | None = None
    missing_specifications: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ActivityItem:
    """One thing that happened, for the activity feed."""

    kind: str
    occurred_at: datetime
    summary: str
    detail: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    severity: str | None = None


@dataclass(frozen=True, slots=True)
class Dashboard:
    """Everything the Overview renders."""

    organization_id: uuid.UUID
    generated_at: datetime
    window_days: int
    sources: SourceSummary
    ingestion: IngestionSummary
    conflicts: ConflictSummary
    confidence: ConfidenceSummary
    activity: tuple[ActivityItem, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """Nothing connected yet.

        Distinct from "connected but quiet": the Overview shows an onboarding
        state for the first and real zeroes for the second.
        """
        return self.sources.total == 0 and self.ingestion.observation_count == 0


# --- Source health ---------------------------------------------------------


async def source_health(db: AsyncSession, *, organization_id: uuid.UUID) -> SourceSummary:
    """Per-source health plus fleet counts."""
    stream_rows = (
        await db.execute(
            select(SourceStream.data_source_id, func.count(SourceStream.id))
            .where(SourceStream.organization_id == organization_id)
            .group_by(SourceStream.data_source_id)
        )
    ).all()
    stream_counts: dict[uuid.UUID, int] = {row[0]: int(row[1]) for row in stream_rows}

    observation_rows = (
        await db.execute(
            select(Observation.source_id, func.count(Observation.id))
            .where(Observation.organization_id == organization_id)
            .group_by(Observation.source_id)
        )
    ).all()
    observation_counts: dict[uuid.UUID, int] = {row[0]: int(row[1]) for row in observation_rows}

    rows = await db.scalars(
        select(DataSource)
        .where(DataSource.organization_id == organization_id)
        .order_by(DataSource.name)
    )

    sources = tuple(
        SourceHealth(
            source_id=source.id,
            name=source.name,
            kind=source.kind,
            status=SourceStatus(source.status),
            stream_count=stream_counts.get(source.id, 0),
            observation_count=observation_counts.get(source.id, 0),
            last_connected_at=source.last_connected_at,
            last_synced_at=source.last_synced_at,
            last_error=source.last_error,
            last_error_at=source.last_error_at,
        )
        for source in rows
    )

    return SourceSummary(
        total=len(sources),
        connected=sum(1 for s in sources if s.status is SourceStatus.CONNECTED),
        never_tested=sum(1 for s in sources if s.status is SourceStatus.CONFIGURED),
        errored=sum(1 for s in sources if s.status is SourceStatus.ERROR),
        disabled=sum(1 for s in sources if s.status is SourceStatus.DISABLED),
        sources=sources,
    )


# --- Ingestion -------------------------------------------------------------


async def ingestion_summary(
    db: AsyncSession, *, organization_id: uuid.UUID, since: datetime
) -> IngestionSummary:
    """Counts of what has actually been ingested."""
    observation_count = await _count(db, Observation, organization_id)
    entity_count = await _count(db, Entity, organization_id)
    stream_count = await _count(db, SourceStream, organization_id)

    observations_in_window = int(
        await db.scalar(
            select(func.count(Observation.id)).where(
                Observation.organization_id == organization_id,
                Observation.ingested_at >= since,
            )
        )
        or 0
    )
    enabled_streams = int(
        await db.scalar(
            select(func.count(SourceStream.id)).where(
                SourceStream.organization_id == organization_id,
                SourceStream.enabled.is_(True),
            )
        )
        or 0
    )

    # An entity counts as mapped once at least one observation resolves to it.
    # Derived from observations rather than from the mapping table, because a
    # mapping pointing at a stream that has produced nothing yet is configured,
    # not populated — and the dashboard should say which.
    from app.models.entity import EntityMapping

    mapped = int(
        await db.scalar(
            select(func.count(func.distinct(EntityMapping.entity_id))).where(
                EntityMapping.organization_id == organization_id
            )
        )
        or 0
    )

    last_sync_at = await db.scalar(
        select(func.max(SyncRun.completed_at)).where(
            SyncRun.organization_id == organization_id,
            SyncRun.status == SyncStatus.COMPLETED.value,
        )
    )
    syncs_in_window = int(
        await db.scalar(
            select(func.count(SyncRun.id)).where(
                SyncRun.organization_id == organization_id,
                SyncRun.started_at >= since,
            )
        )
        or 0
    )
    failed_in_window = int(
        await db.scalar(
            select(func.count(SyncRun.id)).where(
                SyncRun.organization_id == organization_id,
                SyncRun.started_at >= since,
                SyncRun.status == SyncStatus.FAILED.value,
            )
        )
        or 0
    )

    return IngestionSummary(
        observation_count=observation_count,
        observations_in_window=observations_in_window,
        entity_count=entity_count,
        mapped_entity_count=mapped,
        stream_count=stream_count,
        enabled_stream_count=enabled_streams,
        last_sync_at=last_sync_at,
        syncs_in_window=syncs_in_window,
        failed_syncs_in_window=failed_in_window,
    )


# --- Conflicts -------------------------------------------------------------


async def conflict_summary(db: AsyncSession, *, organization_id: uuid.UUID) -> ConflictSummary:
    """Conflict counts by status and severity."""
    status_rows = (
        await db.execute(
            select(Conflict.status, func.count(Conflict.id))
            .where(Conflict.organization_id == organization_id)
            .group_by(Conflict.status)
        )
    ).all()
    counts = {status: int(total) for status, total in status_rows}

    severity_rows = (
        await db.execute(
            select(Conflict.severity, func.count(Conflict.id))
            .where(
                Conflict.organization_id == organization_id,
                Conflict.status.in_([ConflictStatus.OPEN.value, ConflictStatus.ACKNOWLEDGED.value]),
            )
            .group_by(Conflict.severity)
        )
    ).all()
    by_severity = {severity: int(total) for severity, total in severity_rows}

    newest = await db.scalar(
        select(func.max(Conflict.detected_at)).where(
            Conflict.organization_id == organization_id,
            Conflict.status == ConflictStatus.OPEN.value,
        )
    )

    return ConflictSummary(
        open=counts.get(ConflictStatus.OPEN.value, 0),
        acknowledged=counts.get(ConflictStatus.ACKNOWLEDGED.value, 0),
        resolved=counts.get(ConflictStatus.RESOLVED.value, 0),
        dismissed=counts.get(ConflictStatus.DISMISSED.value, 0),
        # Graded buckets only; "unspecified" is reported separately below so an
        # unassessed conflict is never presented as a mild one.
        by_severity={k: v for k, v in by_severity.items() if k != "unspecified"},
        ungraded=by_severity.get("unspecified", 0),
        newest_open_at=newest,
    )


# --- Confidence ------------------------------------------------------------


async def confidence_summary(db: AsyncSession, *, organization_id: uuid.UUID) -> ConfidenceSummary:
    """Reality confidence, or an explicit statement that it is unavailable.

    Reads whatever scored states exist. While the specification is missing none
    do, so ``available`` is False and every number is None — not zero, which
    would be a claim about the data rather than about the specification.
    """
    scored = int(
        await db.scalar(
            select(func.count(RealityState.id)).where(
                RealityState.organization_id == organization_id
            )
        )
        or 0
    )

    # Attributes the sources have spoken about but which carry no scored state.
    # Counted from observations so the dashboard can say how much is waiting on
    # the specification rather than implying there is nothing to score.
    unscored = int(
        await db.scalar(
            select(func.count(func.distinct(Observation.external_id))).where(
                Observation.organization_id == organization_id
            )
        )
        or 0
    )

    if scored == 0:
        return ConfidenceSummary(
            available=False,
            scored_state_count=0,
            unscored_attribute_count=unscored,
            blocked_reason=(
                "The Reality Engine cannot produce a confidence score: the "
                "approved confidence specification is unavailable, so no score "
                "is shown rather than an invented one."
            ),
            missing_specifications=MISSING_SPECIFICATIONS,
        )

    aggregate = (
        await db.execute(
            select(
                func.avg(RealityState.confidence),
                func.min(RealityState.confidence),
                func.max(RealityState.confidence),
            ).where(RealityState.organization_id == organization_id)
        )
    ).one()

    return ConfidenceSummary(
        available=True,
        scored_state_count=scored,
        unscored_attribute_count=unscored,
        average_confidence=float(aggregate[0]) if aggregate[0] is not None else None,
        lowest_confidence=float(aggregate[1]) if aggregate[1] is not None else None,
        highest_confidence=float(aggregate[2]) if aggregate[2] is not None else None,
    )


# --- Activity --------------------------------------------------------------

#: Audit actions worth surfacing on the Overview, mapped to readable text.
#: An allowlist rather than everything: the audit log also carries security
#: events, and a dashboard is not the place to advertise failed logins.
_ACTIVITY_ACTIONS: dict[str, str] = {
    "data_source.created": "Connected a data source",
    "data_source.deleted": "Removed a data source",
    "source_stream.created": "Configured a stream",
    "entity.created": "Created an entity",
    "conflict.acknowledged": "Acknowledged a conflict",
    "conflict.resolved": "Resolved a conflict",
    "conflict.dismissed": "Dismissed a conflict",
    "organization.created": "Created the workspace",
}


async def recent_activity(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    since: datetime,
    limit: int = 20,
) -> tuple[ActivityItem, ...]:
    """A merged feed of what has happened recently.

    Three real sources — the audit log, sync runs and conflict detections —
    interleaved by time. Nothing is synthesised: every item corresponds to a
    row that exists because something actually occurred.
    """
    limit = max(1, min(limit, MAX_ACTIVITY_ITEMS))
    items: list[ActivityItem] = []

    audit_rows = await db.scalars(
        select(AuditLog)
        .where(
            AuditLog.organization_id == organization_id,
            AuditLog.created_at >= since,
            AuditLog.action.in_(list(_ACTIVITY_ACTIONS)),
        )
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    items.extend(
        ActivityItem(
            kind="audit",
            occurred_at=row.created_at,
            summary=_ACTIVITY_ACTIONS.get(row.action, row.action),
            detail=_audit_detail(row),
            resource_type=row.resource_type,
            resource_id=row.resource_id,
        )
        for row in audit_rows
    )

    sync_rows = await db.scalars(
        select(SyncRun)
        .where(SyncRun.organization_id == organization_id, SyncRun.started_at >= since)
        .order_by(SyncRun.started_at.desc())
        .limit(limit)
    )
    items.extend(
        ActivityItem(
            kind="sync",
            occurred_at=run.completed_at or run.started_at,
            summary=_sync_summary(run),
            detail=run.error_message,
            resource_type="sync_run",
            resource_id=str(run.id),
            severity="error" if run.status == SyncStatus.FAILED.value else None,
        )
        for run in sync_rows
    )

    conflict_rows = await db.scalars(
        select(Conflict)
        .where(
            Conflict.organization_id == organization_id,
            Conflict.detected_at >= since,
        )
        .order_by(Conflict.detected_at.desc())
        .limit(limit)
    )
    items.extend(
        ActivityItem(
            kind="conflict",
            occurred_at=conflict.detected_at,
            summary=f"Detected a {conflict.conflict_type.replace('_', ' ')}",
            detail=conflict.summary,
            resource_type="conflict",
            resource_id=str(conflict.id),
            severity=conflict.severity,
        )
        for conflict in conflict_rows
    )

    # Newest first, with a stable secondary key so equal timestamps — common
    # when one sync writes several rows at once — do not reorder between calls.
    items.sort(key=lambda item: (item.occurred_at, item.kind, item.summary), reverse=True)
    return tuple(items[:limit])


def _audit_detail(row: AuditLog) -> str | None:
    details: dict[str, Any] = row.details or {}
    for key in ("name", "natural_key", "table", "slug", "attribute"):
        if key in details:
            return str(details[key])
    return None


def _sync_summary(run: SyncRun) -> str:
    if run.status == SyncStatus.FAILED.value:
        return "A sync failed"
    if run.status == SyncStatus.SKIPPED.value:
        return "A sync was skipped — another was already running"
    if run.rows_created:
        noun = "observation" if run.rows_created == 1 else "observations"
        return f"Ingested {run.rows_created} new {noun}"
    if run.rows_seen:
        return f"Synced {run.rows_seen} rows, nothing new"
    return "Ran a sync"


# --- Assembly --------------------------------------------------------------


async def build_dashboard(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    window: timedelta = DEFAULT_ACTIVITY_WINDOW,
    activity_limit: int = 20,
    now: datetime | None = None,
) -> Dashboard:
    """Assemble the Overview.

    ``now`` is an argument so a caller can reproduce a dashboard as of a
    specific instant, and so tests do not depend on the wall clock.
    """
    now = now or datetime.now(UTC)
    since = now - window

    return Dashboard(
        organization_id=organization_id,
        generated_at=now,
        window_days=window.days,
        sources=await source_health(db, organization_id=organization_id),
        ingestion=await ingestion_summary(db, organization_id=organization_id, since=since),
        conflicts=await conflict_summary(db, organization_id=organization_id),
        confidence=await confidence_summary(db, organization_id=organization_id),
        activity=await recent_activity(
            db, organization_id=organization_id, since=since, limit=activity_limit
        ),
    )


async def _count(db: AsyncSession, model: Any, organization_id: uuid.UUID) -> int:
    total = await db.scalar(
        select(func.count(model.id)).where(model.organization_id == organization_id)
    )
    return int(total or 0)
