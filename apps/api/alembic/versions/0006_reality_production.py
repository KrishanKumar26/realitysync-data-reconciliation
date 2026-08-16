"""reality state productionization

Phase 9 makes reality_states usable while the Phase 0 confidence
specification is still missing. Three changes, each removing a place where the
schema forced a claim nobody could justify.

``confidence`` becomes nullable
    NOT NULL left two options: write 0.0, or write nothing. Phase 5 chose
    nothing, so the table was empty in every deployment and the selection,
    evidence and provenance that need no formula were unreachable. 0.0 would
    have been worse - a zero is a score, and asserting one claims what the
    missing formula would have produced. NULL says "no score exists", which is
    the true statement.

``value`` becomes nullable
    An UNKNOWN state has no value by definition, and a CONTESTED one has none
    while the rule for ranking competing values is unavailable. Neither could
    be stored before.

``value_selected`` is added
    Distinguishes "no selection was made" from "the selected value happens to
    be JSON null". The flag is authoritative for exactly that reason, so there
    is deliberately no constraint tying it to ``value``: a source may assert
    null legitimately, and forbidding that to guard against the ambiguous case
    would trade a real capability for a redundant check. One constraint is
    added - an UNKNOWN state never claims a selection - because that case has
    no such ambiguity.

Also widens the evidence role constraint for ``considered`` - eligible
evidence for a state where nothing was selected, so there is nothing for it to
support or dissent from.

Data-safe forwards: relaxing NOT NULL and widening a CHECK cannot fail on
existing rows, and the new column has a server default. The downgrade refuses
rather than destroys, matching 0005.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_reality_production"
down_revision: str | None = "0005_mysql"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_EVIDENCE_ROLES = "'supporting', 'dissenting', 'excluded'"
_NEW_EVIDENCE_ROLES = "'supporting', 'dissenting', 'excluded', 'considered'"


def upgrade() -> None:
    op.add_column(
        "reality_states",
        sa.Column("value_selected", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.alter_column(
        "reality_states",
        "value",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    )
    op.alter_column(
        "reality_states",
        "confidence",
        existing_type=sa.NUMERIC(precision=4, scale=1),
        nullable=True,
    )
    op.create_check_constraint(
        "unknown_selects_nothing",
        "reality_states",
        "status <> 'unknown' OR NOT value_selected",
    )

    op.drop_constraint("role_valid", "reality_state_evidence", type_="check")
    op.create_check_constraint(
        "role_valid", "reality_state_evidence", f"role IN ({_NEW_EVIDENCE_ROLES})"
    )


def downgrade() -> None:
    # Refuse rather than destroy. Narrowing these columns with unscored states
    # present would either fail at constraint validation or, if the rows were
    # deleted first, discard every derived state in the deployment along with
    # its evidence.
    connection = op.get_bind()
    unscored = connection.scalar(
        sa.text(
            "SELECT count(*) FROM reality_states "
            "WHERE confidence IS NULL OR value IS NULL OR NOT value_selected"
        )
    )
    if unscored:
        raise RuntimeError(
            f"Cannot downgrade: {unscored} reality state(s) have no confidence or no "
            "selected value, which the pre-0006 schema cannot represent. Delete the "
            "derived states first if this is intended - they are recomputable from "
            "observations."
        )
    considered = connection.scalar(
        sa.text("SELECT count(*) FROM reality_state_evidence WHERE role = 'considered'")
    )
    if considered:
        raise RuntimeError(
            f"Cannot downgrade: {considered} evidence row(s) carry the 'considered' role, "
            "which the pre-0006 schema cannot represent."
        )

    op.drop_constraint("role_valid", "reality_state_evidence", type_="check")
    op.create_check_constraint(
        "role_valid", "reality_state_evidence", f"role IN ({_OLD_EVIDENCE_ROLES})"
    )

    op.drop_constraint("unknown_selects_nothing", "reality_states", type_="check")
    op.alter_column(
        "reality_states",
        "confidence",
        existing_type=sa.NUMERIC(precision=4, scale=1),
        nullable=False,
    )
    op.alter_column(
        "reality_states",
        "value",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    )
    op.drop_column("reality_states", "value_selected")
