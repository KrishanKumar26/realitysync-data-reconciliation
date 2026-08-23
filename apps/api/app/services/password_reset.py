"""Forgot-password flow.

Three rules shape everything here.

**A request never reveals whether an account exists.** Both a known and an
unknown address get the same response, the same status code and — as far as
the caller can tell — the same timing. A reset form that answers "no such
account" is a free account-enumeration oracle, and this product has been
careful elsewhere not to hand those out.

**A token is single use and short lived.** It is spent the moment it works,
and every other outstanding token for that user is spent with it. A reset link
that still opens after the password has changed is a live key sitting in an
inbox waiting for whoever reads it next.

**Resetting a password ends every session.** Someone resetting a password is
often doing it precisely because they think someone else is signed in. Leaving
those sessions alive would defeat the point of the exercise.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import generate_token, hash_password, hash_token
from app.models.password_reset import PasswordResetToken
from app.models.user import User
from app.services.auth import revoke_all_user_sessions

logger = get_logger(__name__)

#: Long enough to find the message, short enough that a forwarded or archived
#: link is usually already dead.
TOKEN_LIFETIME = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class ResetRequest:
    """A created token, and where to send it.

    Returned only to the caller that created it — never to an HTTP response.
    The route ignores this entirely when no account matched, which is what
    keeps the two cases indistinguishable from outside.
    """

    user_id: uuid.UUID
    email: str
    token: str
    expires_at: datetime


class InvalidResetToken(Exception):
    """The token is unknown, already spent, or past its expiry.

    One exception for all three on purpose: telling them apart would say
    whether a token ever existed.
    """


async def request_password_reset(
    db: AsyncSession, *, email: str, now: datetime | None = None
) -> ResetRequest | None:
    """Create a reset token for ``email``, or return None if nobody matched.

    The caller must behave identically either way.
    """
    now = now or datetime.now(UTC)
    user = await db.scalar(select(User).where(User.email == email.strip()))

    if user is None or not user.is_active:
        # Logged so an operator can see reset attempts against addresses that
        # do not exist — a signal of enumeration attempts — without the
        # requester learning anything.
        logger.info("password_reset.requested_for_unknown_address")
        return None

    token = generate_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=now + TOKEN_LIFETIME,
        )
    )

    logger.info("password_reset.requested", user_id=str(user.id))
    return ResetRequest(
        user_id=user.id,
        email=user.email,
        token=token,
        expires_at=now + TOKEN_LIFETIME,
    )


async def reset_password(
    db: AsyncSession, *, token: str, new_password: str, now: datetime | None = None
) -> User:
    """Spend a token and set a new password. Raises `InvalidResetToken`.

    Every live session for the user is revoked, and every other outstanding
    reset token for them is spent at the same time.
    """
    now = now or datetime.now(UTC)

    record = await db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token))
    )
    if record is None or record.used_at is not None or record.expires_at <= now:
        logger.warning("password_reset.rejected")
        raise InvalidResetToken

    user = await db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise InvalidResetToken

    user.password_hash = hash_password(new_password)
    record.used_at = now

    # Any other live token for this user dies with it. Two links requested
    # minutes apart must not leave the older one usable after the newer one
    # has already changed the password.
    others = await db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
    )
    for other in others:
        other.used_at = now

    revoked = await revoke_all_user_sessions(db, user_id=user.id, reason="password_reset")

    logger.info("password_reset.completed", user_id=str(user.id), sessions_revoked=revoked)
    return user
