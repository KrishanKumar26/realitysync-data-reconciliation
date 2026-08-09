"""Request correlation.

Every request carries a request_id, propagated from an inbound X-Request-ID
header when present and generated otherwise. It is bound to the logging
context for the request's lifetime and echoed in the response header, so one
identifier correlates a user report, an API log line and (later) an audit row.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger, request_id_ctx

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_INBOUND_LENGTH = 128

logger = get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Bind a request id to the log context and emit an access log line."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        inbound = request.headers.get(REQUEST_ID_HEADER, "")
        # An inbound value is attacker-controlled; bound its length and keep it
        # to characters that are safe in a log field and a response header.
        acceptable = (
            bool(inbound)
            and len(inbound) <= _MAX_INBOUND_LENGTH
            and inbound.isascii()
            and inbound.isprintable()
        )
        request_id = inbound if acceptable else f"req_{uuid.uuid4().hex}"

        token = request_id_ctx.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
            request_id_ctx.reset(token)

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id

        logger.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            request_id=request_id,
        )
        return response
