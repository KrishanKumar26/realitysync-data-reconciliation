"""ORM models.

Importing every model here is what makes Alembic autogenerate work: the
metadata must know about a table before it can diff against the database. A
model that is not imported is a model that silently never gets a migration.

Phase 2 owns identity and tenancy, Phase 3 the ingestion tables, Phase 4 the
entity, reality-state and conflict tables.
"""

from __future__ import annotations

from app.models.audit_log import AuditAction, AuditLog
from app.models.conflict import (
    Conflict,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
)
from app.models.data_source import (
    DataSource,
    SourceCredential,
    SourceKind,
    SourceStatus,
)
from app.models.entity import Entity, EntityMapping
from app.models.membership import ROLE_VALUES, Membership, OrganizationRole
from app.models.observation import EntityMappingState, Observation
from app.models.organization import Organization
from app.models.password_reset import PasswordResetToken
from app.models.reality_state import (
    EvidenceRole,
    RealityState,
    RealityStateEvidence,
    RealityStatus,
)
from app.models.session import Session
from app.models.source_stream import EventTimeSemantics, SourceStream
from app.models.sync_run import SyncRun, SyncStatus
from app.models.user import User

__all__ = [
    "ROLE_VALUES",
    "AuditAction",
    "AuditLog",
    "Conflict",
    "ConflictSeverity",
    "ConflictStatus",
    "ConflictType",
    "DataSource",
    "Entity",
    "EntityMapping",
    "EntityMappingState",
    "EventTimeSemantics",
    "EvidenceRole",
    "Membership",
    "Observation",
    "Organization",
    "OrganizationRole",
    "PasswordResetToken",
    "RealityState",
    "RealityStateEvidence",
    "RealityStatus",
    "Session",
    "SourceCredential",
    "SourceKind",
    "SourceStatus",
    "SourceStream",
    "SyncRun",
    "SyncStatus",
    "User",
]
