"""Uniform error envelope.

Phase 0 §15 defines one response shape for every non-2xx:

    {"error": {"code", "message", "details", "request_id"}}

Unhandled exceptions are logged in full server-side and returned to the client
as a generic message — no stack traces, no driver text, nothing that leaks
internal structure.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)

#: HTTP status -> stable error code, so clients branch on code rather than text.
_STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_ERROR",
    503: "SERVICE_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


def error_response(
    *,
    status_code: int,
    message: str,
    request_id: str | None,
    code: str | None = None,
    details: Any = None,
) -> JSONResponse:
    """Build an error envelope response."""
    payload: dict[str, Any] = {
        "error": {
            "code": code or _STATUS_CODES.get(status_code, "ERROR"),
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }
    return JSONResponse(status_code=status_code, content=payload)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def safe_validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Reduce a validation error to the fields that are safe to return.

    Pydantic's ``errors()`` carries two things that must not leave the process:

    ``input``
        The value that failed. For a password field that is the submitted
        password — echoed into the response body and, through the error log,
        into the log sink. Redaction by key name does not catch it, because the
        key here is the literal string "input".

    ``ctx``
        May hold the original exception object, which is not JSON serialisable.
        Serialising it raised a TypeError that turned every custom-validator
        failure into a 500 instead of a 422.

    Keeping only location, message and type tells the client exactly which
    field is wrong and why, without ever quoting what they sent.
    """
    details: list[dict[str, Any]] = []
    for error in exc.errors():
        location = error.get("loc", ())
        details.append(
            {
                "loc": [str(part) for part in location],
                "msg": str(error.get("msg", "Invalid value")),
                "type": str(error.get("type", "value_error")),
            }
        )
    return details


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers producing the uniform envelope."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return error_response(
            status_code=exc.status_code,
            message=detail,
            request_id=_request_id(request),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            message="Request validation failed",
            request_id=_request_id(request),
            details=safe_validation_details(exc),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "http.unhandled_exception",
            method=request.method,
            path=request.url.path,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="An internal error occurred.",
            request_id=_request_id(request),
        )
