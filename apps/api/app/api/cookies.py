"""Session cookie handling.

Two cookies, with deliberately different properties:

``rs_session``
    HttpOnly. The actual credential. JavaScript must not be able to read it,
    so that an XSS bug cannot exfiltrate a session — the single most valuable
    thing an injected script could steal.

``rs_csrf``
    Readable. Carries the CSRF token, which the client must copy into a request
    header. It has to be readable to serve its purpose, and it is safe to be:
    on its own it authenticates nothing.

``Secure`` and ``SameSite`` come from settings, because the same-site
production deployment and the cross-site staging deployment need different
values and must not need different code. Settings validation already refuses
``SameSite=None`` without ``Secure``, and refuses insecure cookies in
production.
"""

from __future__ import annotations

from starlette.responses import Response

from app.core.config import Settings

#: Cookies are scoped to the whole origin: the API serves every path under one
#: host, and a narrower path would silently drop the cookie on future routes.
_COOKIE_PATH = "/"


def set_session_cookies(
    response: Response,
    *,
    token: str,
    csrf_token: str,
    settings: Settings,
) -> None:
    """Attach the session and CSRF cookies to `response`."""
    max_age = settings.session_lifetime_seconds

    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=max_age,
        path=_COOKIE_PATH,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=max_age,
        path=_COOKIE_PATH,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        # Readable by design — see the module docstring.
        httponly=False,
        samesite=settings.cookie_samesite,
    )


def clear_session_cookies(response: Response, *, settings: Settings) -> None:
    """Remove both cookies.

    Domain, path, secure and samesite must match what was set, or the browser
    treats it as a different cookie and leaves the original in place — a logout
    that appears to work while the cookie survives.

    Clearing the browser's copy is a convenience, not the security control:
    logout revokes the session server-side, so a retained cookie is already
    inert.
    """
    for name in (settings.cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(
            key=name,
            path=_COOKIE_PATH,
            domain=settings.cookie_domain,
            secure=settings.cookie_secure,
            httponly=name == settings.cookie_name,
            samesite=settings.cookie_samesite,
        )
