"""SQLAlchemy declarative base and metadata conventions.

A deterministic constraint-naming convention is established here because
Alembic autogenerate needs stable names to emit correct migrations. Getting
this in place before the first table is written avoids a painful rename pass
once the domain schema lands.

No domain models exist yet — the 21-table schema belongs to Phases 2-5.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    metadata = metadata
