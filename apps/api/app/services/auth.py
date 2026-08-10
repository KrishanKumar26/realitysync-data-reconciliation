"""Authentication and organization context.

Holds the account lifecycle: registration, login, session resolution, logout
and organization switching. Routes stay thin — they translate HTTP to these
calls and back — so the security-relevant logic is in one reviewable place.

Transactions are owned by the caller. Nothing here commits, so a route can
compose several operations and have them succeed or fail together.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import (
    generate_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)
from app.db.tenancy import unscoped
from app.models.membership import Membership, OrganizationRole
from app.models.organization import Organization
from app.models.session import Session
from app.models.user import User

logger = get_logger(__name__)

#: Argon2id verification is run against this even when no account matched, so a
#: request for an unknown address costs the same as one for a known address.
#: Without it, response latency answers "does this email have an account?" —
#: a free user-enumeration oracle on the login endpoint.
_DUMMY_PASSWORD = "rs-timing-equaliser"  # noqa: S105
_dummy_hash: str | None = None


def _dummy_password_hash(settings: Settings) -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(_DUMMY_PASSWORD, settings)
    return _dummy_hash


def reset_dummy_hash_cache() -> None:
    """Drop the cached dummy hash. Used by tests that change Argon2 cost."""
    global _dummy_hash
    _dummy_hash = None


class AuthError(Exception):
    """Base class for authentication failures that routes translate to HTTP."""


class InvalidCredentialsError(AuthError):
    """Wrong email, wrong password, or a disabled account.

    Deliberately one exception for all three. Distinguishing them in the
    response would tell an attacker which addresses have accounts.
    """


class EmailAlreadyRegisteredError(AuthError):
    """Registration attempted with an address that already exists."""


class NotAMemberError(AuthError):
    """The user is not a member of the requested organization."""


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Everything the request needs to know about who is calling.

    Assembled once per request by the session dependency and passed around,
    rather than re-queried by each layer.
    """

    user: User
    session: Session
    memberships: tuple[Membership, ...]
    active_membership: Membership | None

    @property
    def organization_id(self) -> uuid.UUID | None:
        return self.active_membership.organization_id if self.active_membership else None

    @property
    def role(self) -> OrganizationRole | None:
        return self.active_membership.role_enum if self.active_membership else None


# --- Slugs -----------------------------------------------------------------

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_SLUG_TRIM = re.compile(r"^-+|-+$")


def slugify(value: str) -> str:
    """Turn an organization name into a URL-safe slug.

    Unicode is normalised to its closest ASCII form first, so "Café Ltd"
    becomes "cafe-ltd" rather than losing the word entirely.
    """
    normalised = unicodedata.normalize("NFKD", value)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_TRIM.sub("", _SLUG_STRIP.sub("-", ascii_only))
    return slug[:48]


async def unique_slug(db: AsyncSession, base: str) -> str:
    """Find a free slug near `base`.

    Advisory only: two concurrent registrations can still pick the same slug,
    and the unique index is what actually prevents a duplicate. The caller
    retries on IntegrityError.
    """
    # A name of "!!!" slugifies to nothing, and the CHECK constraint requires
    # at least two characters.
    candidate = base or f"org-{uuid.uuid4().hex[:8]}"
    if len(candidate) < 2:
        candidate = f"{candidate}-{uuid.uuid4().hex[:6]}"

    existing = await db.scalar(select(Organization.slug).where(Organization.slug == candidate))
    if existing is None:
        return candidate

    for suffix in range(2, 12):
        attempt = f"{candidate[:44]}-{suffix}"
        taken = await db.scalar(select(Organization.slug).where(Organization.slug == attempt))
        if taken is None:
            return attempt

    return f"{candidate[:39]}-{uuid.uuid4().hex[:8]}"


# --- Registration ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    user: User
    organization: Organization
    membership: Membership


async def register_account(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    organization_name: str,
    settings: Settings | None = None,
) -> RegistrationResult:
    """Create a user, their first organization, and an owner membership.

    This is the only way real records enter the system. There is no seeding and
    no fixture data: every user and organization in any environment came
    through this function.
    """
    settings = settings or get_settings()
    normalised_email = email.strip()

    # Pre-check for a clear error message. The unique index is the real
    # guarantee — two simultaneous registrations both pass this check.
    existing = await db.scalar(select(User.id).where(User.email == normalised_email))
    if existing is not None:
        raise EmailAlreadyRegisteredError

    user = User(
        email=normalised_email,
        password_hash=hash_password(password, settings),
        full_name=full_name.strip(),
    )
    db.add(user)

    organization = Organization(
        name=organization_name.strip(),
        slug=await unique_slug(db, slugify(organization_name)),
    )
    db.add(organization)

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_users_email" in str(exc.orig):
            raise EmailAlreadyRegisteredError from exc
        raise

    membership = Membership(
        user_id=user.id,
        organization_id=organization.id,
        role=OrganizationRole.OWNER.value,
    )
    db.add(membership)
    await db.flush()

    return RegistrationResult(user=user, organization=organization, membership=membership)


# --- Login -----------------------------------------------------------------


async def authenticate(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    settings: Settings | None = None,
) -> User:
    """Return the user for valid credentials, or raise InvalidCredentialsError.

    Runs a hash verification in every path, including "no such user", so that
    timing does not reveal whether an address is registered.
    """
    settings = settings or get_settings()
    user = await db.scalar(select(User).where(User.email == email.strip()))

    if user is None:
        verify_password(password, _dummy_password_hash(settings), settings)
        raise InvalidCredentialsError

    if not verify_password(password, user.password_hash, settings):
        raise InvalidCredentialsError

    if not user.is_active:
        # Checked after verification so a disabled account is indistinguishable
        # from a wrong password, in both response and timing.
        raise InvalidCredentialsError

    # Raising the Argon2 cost should reach existing accounts without a reset.
    if needs_rehash(user.password_hash, settings):
        user.password_hash = hash_password(password, settings)

    user.last_login_at = datetime.now(UTC)
    return user


# --- Memberships -----------------------------------------------------------


async def list_user_memberships(db: AsyncSession, user_id: uuid.UUID) -> tuple[Membership, ...]:
    """Every membership belonging to `user_id`, across all organizations.

    One of the few legitimately cross-tenant reads in the product, hence the
    explicit `unscoped()`: "which organizations do I belong to" is the question
    the organization selector asks, and it cannot be answered from inside a
    single tenant. The query is still bounded to one user's own rows, so no
    data belonging to anyone else is reachable through it.
    """
    with unscoped():
        result = await db.scalars(
            select(Membership).where(Membership.user_id == user_id).order_by(Membership.created_at)
        )
        return tuple(result.all())


async def get_membership(
    db: AsyncSession, *, user_id: uuid.UUID, organization_id: uuid.UUID
) -> Membership | None:
    """The user's membership in one organization, or None."""
    membership: Membership | None = await db.scalar(
        select(Membership).where(
            Membership.organization_id == organization_id,
            Membership.user_id == user_id,
        )
    )
    return membership


async def load_organizations(
    db: AsyncSession, organization_ids: tuple[uuid.UUID, ...]
) -> dict[uuid.UUID, Organization]:
    """Fetch organizations by id, keyed for lookup.

    `organizations` is not tenant-owned — it *is* the tenant — so this needs no
    scoping. Callers pass only ids taken from the caller's own memberships.
    """
    if not organization_ids:
        return {}
    rows = await db.scalars(select(Organization).where(Organization.id.in_(organization_ids)))
    return {org.id: org for org in rows}


# --- Sessions --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A newly created session and the secrets the client must receive.

    `token` is the only time the raw session token exists outside the client:
    the database stores its hash. It is returned here so the route can set the
    cookie, and it is never logged.
    """

    session: Session
    token: str
    csrf_token: str


async def create_session(
    db: AsyncSession,
    *,
    user: User,
    active_organization_id: uuid.UUID | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    settings: Settings | None = None,
) -> IssuedSession:
    """Issue a session for `user`."""
    settings = settings or get_settings()
    token = generate_token()
    csrf_token = generate_token()
    now = datetime.now(UTC)

    session = Session(
        user_id=user.id,
        token_hash=hash_token(token),
        # Truncated to the column width; both tokens are 43 characters, so this
        # is a guard against a future change, not a live constraint.
        csrf_token=csrf_token[:64],
        active_organization_id=active_organization_id,
        issued_at=now,
        expires_at=now + timedelta(seconds=settings.session_lifetime_seconds),
        last_seen_at=now,
        ip_address=ip_address,
        user_agent=user_agent[:1000] if user_agent else None,
    )
    db.add(session)
    await db.flush()

    return IssuedSession(session=session, token=token, csrf_token=csrf_token)


@dataclass(frozen=True, slots=True)
class SessionRejection:
    """Why a session was not accepted. Never sent to the client verbatim."""

    reason: str


async def resolve_session(
    db: AsyncSession,
    *,
    token: str,
    settings: Settings | None = None,
) -> AuthContext | SessionRejection:
    """Turn a raw cookie token into an :class:`AuthContext`.

    Returns a rejection rather than raising, so the caller decides the HTTP
    consequence — a protected route returns 401, while "who am I" returns a
    signed-out state with 200.
    """
    settings = settings or get_settings()

    session = await db.scalar(select(Session).where(Session.token_hash == hash_token(token)))
    if session is None:
        return SessionRejection(reason="unknown")

    now = datetime.now(UTC)

    if session.revoked_at is not None:
        return SessionRejection(reason="revoked")
    if session.expires_at <= now:
        return SessionRejection(reason="expired")
    if session.last_seen_at + timedelta(seconds=settings.session_idle_timeout_seconds) <= now:
        return SessionRejection(reason="idle_timeout")

    user = await db.get(User, session.user_id)
    if user is None:
        return SessionRejection(reason="unknown_user")
    if not user.is_active:
        return SessionRejection(reason="user_disabled")

    # Throttled so an active session does not cause a write on every request.
    if (now - session.last_seen_at).total_seconds() >= settings.session_touch_interval_seconds:
        session.last_seen_at = now

    memberships = await list_user_memberships(db, user.id)
    active = next(
        (m for m in memberships if m.organization_id == session.active_organization_id),
        None,
    )

    # A session can outlive the membership that gave it its organization only if
    # the composite foreign key did not fire — it always should. Falling back to
    # "no active organization" keeps the request answerable instead of 500ing.
    if session.active_organization_id is not None and active is None:
        logger.warning(
            "auth.session.active_membership_missing",
            session_id=str(session.id),
            user_id=str(user.id),
        )
        session.active_organization_id = None

    return AuthContext(
        user=user,
        session=session,
        memberships=memberships,
        active_membership=active,
    )


async def revoke_session(db: AsyncSession, session: Session, *, reason: str = "logout") -> None:
    """End a session.

    The row is kept and stamped rather than deleted, so "when did this session
    end and why" stays answerable. Because lookup is by token_hash and the
    resolver rejects anything with revoked_at set, the cookie is dead
    immediately — there is no window where an old token still works.
    """
    if session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        session.revoked_reason = reason[:64]


async def revoke_all_user_sessions(
    db: AsyncSession, *, user_id: uuid.UUID, reason: str, keep_session_id: uuid.UUID | None = None
) -> int:
    """Revoke every live session for a user. Returns how many were ended."""
    rows = await db.scalars(
        select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
    )
    count = 0
    for session in rows:
        if keep_session_id is not None and session.id == keep_session_id:
            continue
        await revoke_session(db, session, reason=reason)
        count += 1
    return count


async def switch_organization(
    db: AsyncSession,
    *,
    context: AuthContext,
    organization_id: uuid.UUID,
) -> Membership:
    """Point the caller's session at a different organization.

    Membership is verified here, and the composite foreign key verifies it
    again on flush. Two independent checks, because this is the operation that
    decides which tenant's data the next request sees.
    """
    membership = next(
        (m for m in context.memberships if m.organization_id == organization_id),
        None,
    )
    if membership is None:
        raise NotAMemberError

    context.session.active_organization_id = organization_id
    await db.flush()
    return membership
