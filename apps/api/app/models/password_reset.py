"""Password reset tokens.

Deliberately a table rather than a column on ``users``. A column holds one
outstanding request and silently overwrites the previous one, which turns two
resets started minutes apart into a confusing race; a table lets every request
be recorded, expired and audited independently.

Only the SHA-256 of the token is stored, exactly as sessions do. Anyone with
read access to this table learns nothing they can use: the token is 256 bits
of CSPRNG output, so a fast hash is the correct choice and not a weakness —
there is no password-shaped guessable space to attack.

**Not tenant-owned.** A reset belongs to a person, not to a workspace, and a
person can belong to several. It is deliberately absent from the tenancy
guard's table list for that reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import TimestampMixin, TimestampTZ, uuid_pk


class PasswordResetToken(Base, TimestampMixin):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: SHA-256 of the emailed token, hex-encoded. The token itself is never
    #: stored, so a database dump cannot be used to reset anyone's password.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    expires_at: Mapped[datetime] = mapped_column(TimestampTZ, nullable=False)

    #: Set the moment the token is spent. Single use: a reset link that still
    #: works after the password has changed is a link sitting in an inbox
    #: waiting to be found by whoever reads it next.
    used_at: Mapped[datetime | None] = mapped_column(TimestampTZ, nullable=True)

    __table_args__ = (
        # Every lookup is "this user's live tokens", for invalidating the rest
        # once one is spent.
        Index("ix_password_reset_tokens_user_id", "user_id"),
    )
