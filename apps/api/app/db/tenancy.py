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
* :func:`unscoped` is the only way past the guard, it must be used deliberately,
  and every use is expected to carry a comment justifying it.

The guard is a backstop, not a substitute for writing the filter. Application
code should still scope explicitly — but now forgetting is a loud failure in
development and in tests rather than a quiet breach in production.

Known limitation: the guard inspects WHERE and JOIN-ON clauses of the emitted
statement. A tenant-owned table reached only through a correlated subquery in a
SELECT list would not be detected. Filters written normally are covered, and
the schema-level defences (NOT NULL ``organization_id``, the composite session
foreign key) hold regardless.
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
from sqlalchemy.sql import visitors
from sqlalchemy.sql.expression import Delete, Join, Select, Update

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


class OrganizationScoped:
    """Mixin declaring a row as owned by exactly one organization.

    ``ON DELETE CASCADE``: deleting a tenant removes its data. There is no
    meaningful orphan state for a tenant-owned row, and leaving rows behind
    whose organization no longer exists would create records that no query can
    legitimately reach.
    """

    @declared_attr
    @classmethod
    def organization_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            PGUUID(as_uuid=True),
            ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )

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


def _tenant_tables_constrained(statement: Any) -> set[str]:
    """Tenant-owned table names whose organization_id is referenced in a filter.

    Both WHERE clauses and JOIN-ON clauses count: joining a tenant-owned table
    on ``organization_id`` constrains it just as effectively as a WHERE.
    """
    clauses: list[Any] = []

    whereclause = getattr(statement, "whereclause", None)
    if whereclause is not None:
        clauses.append(whereclause)

    for element in visitors.iterate(statement):
        if isinstance(element, Join) and element.onclause is not None:
            clauses.append(element.onclause)

    constrained: set[str] = set()
    for clause in clauses:
        for element in visitors.iterate(clause):
            if (
                isinstance(element, Column)
                and element.name == ORGANIZATION_ID
                and element.table is not None
                and element.table.name in TENANT_OWNED_TABLES
            ):
                constrained.add(element.table.name)
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
