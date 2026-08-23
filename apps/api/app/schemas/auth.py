"""Authentication request and response models.

These types are the API boundary. ORM objects never cross it — which is also
the structural reason a password hash cannot leak: :class:`UserResponse` has no
field for one, and Pydantic serialises declared fields only. Preventing the
leak is a property of the type, not of every route remembering to exclude it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.config import get_settings
from app.models.membership import OrganizationRole

#: Applied to both registration and login. Trimming would silently change a
#: password that legitimately begins or ends with a space, so it is not done.
PasswordStr = Annotated[str, Field(min_length=1, max_length=1024)]


def _validate_password_policy(value: str) -> str:
    settings = get_settings()
    if len(value) < settings.password_min_length:
        raise ValueError(f"Password must be at least {settings.password_min_length} characters")
    if len(value) > settings.password_max_length:
        raise ValueError(f"Password must be at most {settings.password_max_length} characters")
    if not value.strip():
        raise ValueError("Password must not be entirely whitespace")
    return value


class RegisterRequest(BaseModel):
    """Self-serve signup: creates the user and their first organization."""

    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")

    email: EmailStr
    password: PasswordStr
    full_name: Annotated[str, Field(min_length=1, max_length=200)]
    organization_name: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        return _validate_password_policy(value)

    @field_validator("full_name", "organization_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Value must not be blank")
        return stripped


class LoginRequest(BaseModel):
    """Credentials. No policy validation — an old password that predates a
    policy change must still be able to log in and be rehashed."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: PasswordStr


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """A token and the password to set.

    The same policy validator as registration, deliberately. A reset is exactly
    the moment a weak password would otherwise walk past the rules that guard
    the sign-up form — and someone resetting under pressure is more likely, not
    less, to reach for something short.
    """

    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, Field(min_length=16, max_length=256)]
    password: PasswordStr

    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        return _validate_password_policy(value)


class SwitchOrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: uuid.UUID


class UserResponse(BaseModel):
    """A user as the API presents them.

    No password_hash field, deliberately and permanently.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    created_at: datetime
    last_login_at: datetime | None = None


class OrganizationMembershipResponse(BaseModel):
    """An organization the caller belongs to, with their role in it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    role: OrganizationRole


class AuthenticatedSessionResponse(BaseModel):
    """The signed-in shape of `GET /api/auth/session`."""

    authenticated: Literal[True] = True
    user: UserResponse
    organizations: list[OrganizationMembershipResponse]
    active_organization_id: uuid.UUID | None
    #: Echoed so a client that lost the readable cookie (a fresh tab, a cleared
    #: cookie jar) can recover it without re-authenticating. Safe to expose:
    #: the CSRF token authenticates nothing on its own — it only proves the
    #: request came from a context that could read the response.
    csrf_token: str
    expires_at: datetime


class AnonymousSessionResponse(BaseModel):
    """The signed-out shape of `GET /api/auth/session`.

    Returned with 200, not 401. "Am I signed in?" is a question the endpoint
    answers successfully; "no" is a valid answer, and making it an error forces
    every client to treat a normal state as an exception.
    """

    authenticated: Literal[False] = False
    #: Distinguishes "never signed in" from "your session ended", so the
    #: interface can say which. Never explains *why* beyond a coarse category.
    reason: Literal["anonymous", "expired"] = "anonymous"


class MessageResponse(BaseModel):
    """A sentence for the user. Used where there is no record to return.

    Password reset answers with one of these on purpose: returning anything
    shaped like a user would say whether the address exists.
    """

    model_config = ConfigDict(extra="forbid")

    message: str


class LogoutResponse(BaseModel):
    ok: Literal[True] = True
