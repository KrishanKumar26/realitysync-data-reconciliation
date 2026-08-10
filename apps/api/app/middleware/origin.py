"""Origin validation for state-changing requests.

CSRF tokens protect authenticated requests, but login and registration have no
session yet and therefore no token. That leaves login CSRF: a foreign page can
POST credentials the attacker controls and silently sign the victim into an
account the attacker owns, so anything the victim then does happens inside the
attacker's workspace.

The defence is the ``Origin`` header. Browsers set it on every cross-origin
request and it cannot be forged from JavaScript, so a request that arrives with
a foreign Origin is rejected before it reaches a route.

A *missing* Origin is allowed. Non-browser clients — curl, server-to-server
integrations, health checkers — do not send one, and they are not subject to
CSRF in the first place because no ambient cookie gets attached. Rejecting them
would break legitimate API use to defend against an attack they cannot suffer.

This runs in addition to CORS, not instead of it. CORS governs what a browser
does with a *response*; by then the request has already been processed. For a
state-changing request that is too late, so the check happens here on the way in.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.logging import get_logger

logger = get_logger(__name__)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginValidationMiddleware(BaseHTTPMiddleware):
    """Reject state-changing requests carrying a disallowed Origin."""

    def __init__(self, app: Callable[..., object], *, allowed_origins: list[str]) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._allowed = frozenset(allowed_origins)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and origin not in self._allowed:
                request_id = getattr(request.state, "request_id", None)
                logger.warning(
                    "http.origin_rejected",
                    method=request.method,
                    path=request.url.path,
                    origin=origin,
                )
                # Built directly rather than raised: middleware sits outside the
                # exception handlers, so the envelope is constructed here to keep
                # the response shape identical to every other error.
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "FORBIDDEN",
                            "message": "Request origin is not allowed.",
                            "details": None,
                            "request_id": request_id,
                        }
                    },
                )
        return await call_next(request)
