"""foundation: required PostgreSQL extensions

Establishes the migration baseline and enables the extensions the approved
schema depends on. No domain tables are created here — those arrive with
their owning phases.

citext backs case-insensitive unique columns (users.email,
organizations.slug). It is a trusted extension from PostgreSQL 13 onward, so
the database owner can create it without superuser rights.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-08-10

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS citext")
