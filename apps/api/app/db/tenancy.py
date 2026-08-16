"""Multi-tenant scoping enforcement.

RealitySync is multi-tenant: an organization is a tenant, and organization-owned
records must never be read across tenant boundaries. Relying on every future
query remembering ``.where(Model.organization_id == org_id)`` is not a control —
it is a hope. One forgotten filter silently leaks another customer's data, and
nothing fails loudly.

So the rule is enforced by the session itself:

* :class:`OrganizationScoped` declares the ``organization_id`` column and
  registers the table as tenant-owned.
* A ``do_orm_execute`` listener inspects every ORM SELECT/UPDATE/DELETE. If it
  touches a tenant-owned table without constraining that table's
  ``organization_id``, it raises :class:`MissingOrganizationScopeError` instead
  of returning rows.

  "Constraining" means comparing ``organization_id`` with ``==`` or ``IN``
  against a **bound value**. Merely mentioning the column is not enough - see
  :func:`_pins_a_tenant` for the shapes that mention it and scope nothing, all
  of which were verified to bypass an earlier version of this guard.
* :func:`unscoped` is the only way past the guard, it must be used deliberately,
  and every use is expected to carry a comment justifying it.

The guard is a backstop, not a substitute for writing the filter. Application
code should still scope explicitly — but now forgetting is a loud failure in
development and in tests rather than a quiet breach in production.

Known limitations, stated so nobody mistakes the backstop for the whole
defence. The full list, with what was and was not tested, is in
docs/security.md:

* A tenant-owned table reached only through a correlated subquery in a SELECT
  list is not detected.
* Only shapes are checked, never values. The guard confirms a query pins
  ``organization_id`` to *some* bound value; that it is the *caller's* value is
  the route layer's job.
* Raw ``text()`` SQL and ``INSERT`` are not inspected at all.

The schema-level defences - NOT NULL ``organization_id``, the composite session
foreign key, ON DELETE CASCADE - hold regardless of any of this.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import Column, ForeignKey, Table, event
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, declared_attr, mapped_column
from sqlalchemy.orm.session import ORMExecuteState
from sqlalchemy.sql import operators, visitors
from sqlalchemy.sql.expression import BinaryExpression, BindParameter, Delete, Join, Select, Update

ORGANIZATION_ID = "organization_id"

#: Names of tables that carry tenant-owned rows. Populated by the mixin.
TENANT_OWNED_TABLES: set[str] = set()

#: Depth counter for :func:`unscoped`. A ContextVar rather than a plain global
#: because requests run concurrently in one process: each asyncio task gets its
#: own copy of the context, so one request's escape hatch cannot widen another's.
_unscoped_depth: ContextVar[int] = ContextVar("tenancy_unscoped_depth", default=0)


class MissingOrganizationScopeError(RuntimeError):
    """A tenant-owned table was queried without an organization_id filter.

    This is a programming error, not a user error. It must never be converted
    into a 4xx: the correct response is to fix the query.
    """

    def __init__(self, tables: set[str]) -> None:
        listed = ", ".join(sorted(tables))
        super().__init__(
            f"Query touches tenant-owned table(s) [{listed}] without constraining "
            f"{ORGANIZATION_ID}. Scope the query to a single organization, or wrap "
            f"it in app.db.tenancy.unscoped() if it is genuinely cross-tenant."
        )
        self.tables = tables


def organization_id_column(*, index: bool = True) -> Mapped[uuid.UUID]:
    """The tenant foreign key.

    Set ``index=False`` when another index already leads with
    ``organization_id`` — PostgreSQL uses a composite index's leftmost prefix,
    so a second single-column index would be a duplicate to maintain on every
    write for no read benefit.
    """
    return mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=index,
    )


class OrganizationScoped:
    """Mixin declaring a row as owned by exactly one organization.

    ``ON DELETE CASCADE``: deleting a tenant removes its data. There is no
    meaningful orphan state for a tenant-owned row, and leaving rows behind
    whose organization no longer exists would create records that no query can
    legitimately reach.

    Subclasses may redeclare ``organization_id`` with
    ``organization_id_column(index=False)`` to suppress the default index; the
    guard registration below is independent of how the column is declared.
    """

    @declared_attr
    @classmethod
    def organization_id(cls) -> Mapped[uuid.UUID]:
        return organization_id_column()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        table_name = getattr(cls, "__tablename__", None)
        if isinstance(table_name, str):
            TENANT_OWNED_TABLES.add(table_name)


@contextmanager
def unscoped() -> Iterator[None]:
    """Temporarily allow cross-tenant queries.

    Legitimate uses are rare and each one should say why in a comment. The
    canonical example is enumerating the organizations a user belongs to: that
    question spans tenants by definition, so it cannot be tenant-scoped.
    """
    token = _unscoped_depth.set(_unscoped_depth.get() + 1)
    try:
        yield
    finally:
        _unscoped_depth.reset(token)


def _tenant_tables_referenced(statement: Any) -> set[str]:
    """Tenant-owned table names appearing anywhere in the statement."""
    referenced: set[str] = set()
    for element in visitors.iterate(statement):
        if isinstance(element, Table) and element.name in TENANT_OWNED_TABLES:
            referenced.add(element.name)
    return referenced


#: Operators that genuinely pin a table to a tenant. Deliberately just these
#: two. ``!=`` compares against a bound value and reads every *other* tenant,
#: which is the opposite of scoping; ``IS NOT NULL`` is structurally a filter
#: and semantically none, since the column is NOT NULL anyway.
_PINNING_OPERATORS = frozenset({operators.eq, operators.in_op})


def _pins_a_tenant(clause: Any) -> set[str]:
    """Tenant tables that `clause` pins to a specific organization.

    A table counts as constrained only when its ``organization_id`` is compared
    with ``==`` or ``IN`` **against a bound value**. Presence of the column in a
    filter is not enough, and this is the point:

        WHERE observations.organization_id = entities.organization_id
        WHERE observations.organization_id IS NOT NULL
        WHERE observations.organization_id != :org

    All three mention the column, all three satisfied the previous check, and
    none of them restricts the query to one tenant. The first two were verified
    to bypass the guard before this was tightened. Nothing in the application
    writes queries that way, which is exactly why a guard has to reject them —
    it exists to catch the query nobody meant to write.
    """
    pinned: set[str] = set()

    for element in visitors.iterate(clause):
        if not isinstance(element, BinaryExpression):
            continue
        if element.operator not in _PINNING_OPERATORS:
            continue

        for side, other in ((element.left, element.right), (element.right, element.left)):
            if (
                isinstance(side, Column)
                and side.name == ORGANIZATION_ID
                and side.table is not None
                and side.table.name in TENANT_OWNED_TABLES
                # The other side must be a value, not another column. Comparing
                # two tenant tables' organization_id to each other correlates
                # them without pinning either.
                and isinstance(other, BindParameter)
            ):
                pinned.add(side.table.name)
    return pinned


def _tenant_tables_constrained(statement: Any) -> set[str]:
    """Tenant-owned table names whose organization_id is pinned to a tenant.

    Three places count, because all three genuinely constrain the rows read:

    * the statement's own WHERE clause;
    * the WHERE clause of any nested SELECT — a scalar subquery computing a
      per-row count is filtered by its own WHERE, not the outer one;
    * any JOIN-ON clause, since joining on ``organization_id`` constrains a
      table just as effectively as a WHERE.

    Missing the nested cases would make the guard reject correctly-scoped
    queries, and a guard that cries wolf gets switched off.
    """
    clauses: list[Any] = []

    for element in visitors.iterate(statement):
        if isinstance(element, (Select, Update, Delete)):
            nested_where = element.whereclause
            if nested_where is not None:
                clauses.append(nested_where)
        elif isinstance(element, Join) and element.onclause is not None:
            clauses.append(element.onclause)

    # `iterate` does not always yield the top-level statement itself.
    whereclause = getattr(statement, "whereclause", None)
    if whereclause is not None:
        clauses.append(whereclause)

    constrained: set[str] = set()
    for clause in clauses:
        constrained |= _pins_a_tenant(clause)
    return constrained


def assert_organization_scoped(statement: Any) -> None:
    """Raise if `statement` reads or writes tenant-owned rows unscoped."""
    if _unscoped_depth.get() > 0:
        return
    if not isinstance(statement, (Select, Update, Delete)):
        return

    referenced = _tenant_tables_referenced(statement)
    if not referenced:
        return

    unconstrained = referenced - _tenant_tables_constrained(statement)
    if unconstrained:
        raise MissingOrganizationScopeError(unconstrained)


@event.listens_for(Session, "do_orm_execute")
def _guard_orm_execute(state: ORMExecuteState) -> None:
    """Session-wide enforcement point.

    Registered on the ORM Session class, so it covers every session in the
    process — including the sync session that AsyncSession drives underneath,
    and including lazy loads, which are the easiest place to leak a tenant
    boundary by accident.
    """
    assert_organization_scoped(state.statement)
