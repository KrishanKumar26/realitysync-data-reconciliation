"""mysql connector: widen the source kind constraint

Phase 8 adds a second connector type. Everything else it needed was a class
and a registry entry, exactly as the DataConnector documentation claimed -
nothing in app/ingestion, app/engine or the API changed.

The database is the one exception, and it is a deliberate one. ``kind`` is
constrained by a CHECK rather than left free text, so a typo cannot create a
source no connector can build. That safety has a cost: adding a type is a
migration. The trade is worth it - an unbuildable source row would fail at
sync time, in production, long after the mistake was made.

Recorded here so the next connector's author knows the full checklist:

    1. A DataConnector implementation
    2. A SourceKind value
    3. A registry entry
    4. This migration, widened

Data-safe in both directions. Widening a CHECK cannot fail on existing rows.
The downgrade deliberately refuses if any mysql source exists rather than
silently dropping the constraint or the rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_mysql"
down_revision = "0004_reality"
branch_labels = None
depends_on = None

#: The bare name. Alembic's naming convention prefixes it with
#: ``ck_data_sources_``; passing the full name produces a doubled prefix.
_CONSTRAINT = "kind_valid"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "data_sources", type_="check")
    op.create_check_constraint(_CONSTRAINT, "data_sources", "kind IN ('postgresql', 'mysql')")


def downgrade() -> None:
    # Refuse rather than destroy. Narrowing the constraint with mysql sources
    # present would either fail obscurely at constraint validation or, if the
    # rows were deleted first, discard a customer's configured source and every
    # observation hanging off it.
    connection = op.get_bind()
    remaining = connection.scalar(sa.text("SELECT count(*) FROM data_sources WHERE kind = 'mysql'"))
    if remaining:
        raise RuntimeError(
            f"Cannot downgrade: {remaining} MySQL data source(s) exist. "
            "Remove them first if this is intended."
        )

    op.drop_constraint(_CONSTRAINT, "data_sources", type_="check")
    op.create_check_constraint(_CONSTRAINT, "data_sources", "kind IN ('postgresql')")
