"""ORM models.

Importing every model here is what makes Alembic autogenerate work: the
metadata must know about a table before it can diff against the database. A
model that is not imported is a model that silently never gets a migration.

Phase 2 owns the identity and tenancy tables. The observation, entity and
reality tables arrive in Phases 3-5.
"""

from __future__ import annotations

from app.models.audit_log import AuditAction, AuditLog
from app.models.membership import ROLE_VALUES, Membership, OrganizationRole
from app.models.organization import Organization
from app.models.session import Session
from app.models.user import User

__all__ = [
    "ROLE_VALUES",
    "AuditAction",
    "AuditLog",
    "Membership",
    "Organization",
    "OrganizationRole",
    "Session",
    "User",
]
