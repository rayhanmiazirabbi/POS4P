from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

#: Mirrors the ``DomainErrorCode`` union exported by ``@pharmacy/core``.
ERROR_STATUS: dict[str, int] = {
    "VALIDATION_ERROR": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "UNAUTHORIZED": status.HTTP_401_UNAUTHORIZED,
    "FORBIDDEN": status.HTTP_403_FORBIDDEN,
    "NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "CONFLICT": status.HTTP_409_CONFLICT,
    "IDEMPOTENCY_CONFLICT": status.HTTP_409_CONFLICT,
    "INSUFFICIENT_STOCK": status.HTTP_409_CONFLICT,
    "RATE_LIMITED": status.HTTP_429_TOO_MANY_REQUESTS,
    "STORE_CONTEXT_REQUIRED": status.HTTP_400_BAD_REQUEST,
    "DEVICE_CONTEXT_REQUIRED": status.HTTP_400_BAD_REQUEST,
    "INTERNAL_ERROR": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


class DomainError(Exception):
    """Business-rule failure that maps onto the shared frontend error taxonomy."""

    code = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        field_errors: dict[str, list[str]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.field_errors = field_errors
        self.details = details

    @property
    def status_code(self) -> int:
        return ERROR_STATUS.get(self.code, status.HTTP_500_INTERNAL_SERVER_ERROR)


class ValidationError(DomainError):
    code = "VALIDATION_ERROR"


class Unauthorized(DomainError):
    code = "UNAUTHORIZED"


class Forbidden(DomainError):
    code = "FORBIDDEN"


class NotFound(DomainError):
    code = "NOT_FOUND"


class Conflict(DomainError):
    code = "CONFLICT"


class IdempotencyConflict(DomainError):
    code = "IDEMPOTENCY_CONFLICT"


class RateLimited(DomainError):
    code = "RATE_LIMITED"


def error_body(
    code: str,
    message: str,
    request_id: str,
    field_errors: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Build the wire shape consumed by ``decodeApiError`` in ``@pharmacy/api``."""
    body: dict[str, Any] = {"code": code, "message": message, "requestId": request_id}
    if field_errors:
        body["fieldErrors"] = field_errors
    return body


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, _request_id(request), exc.field_errors),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        field_errors: dict[str, list[str]] = {}
        for error in exc.errors():
            location = [str(part) for part in error["loc"] if part not in ("body", "query", "path")]
            field_errors.setdefault(".".join(location) or "_", []).append(error["msg"])
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_body(
                "VALIDATION_ERROR", "Request validation failed", _request_id(request), field_errors
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            code = str(detail["code"])
            message = str(detail.get("message", "Request failed"))
        else:
            code = next(
                (key for key, value in ERROR_STATUS.items() if value == exc.status_code),
                "INTERNAL_ERROR",
            )
            message = str(detail) if detail else "Request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message, _request_id(request)),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body("INTERNAL_ERROR", "Internal server error", _request_id(request)),
        )
