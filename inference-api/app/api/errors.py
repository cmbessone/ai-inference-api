"""Traducción de errores de dominio a HTTP. Único lugar que conoce status codes de error."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import (
    DomainError,
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
    OverloadedError,
    RateLimitExceededError,
    UnauthorizedError,
    UnknownModelError,
    UnknownModelVersionError,
)

_STATUS_BY_ERROR: dict[type[DomainError], int] = {
    UnauthorizedError: 401,
    RateLimitExceededError: 429,
    UnknownModelError: 400,
    UnknownModelVersionError: 400,
    OverloadedError: 503,
    LLMUnavailableError: 503,
    LLMTimeoutError: 504,
    LLMProviderError: 502,
}


def status_for(error: DomainError) -> int:
    return next((s for t, s in _STATUS_BY_ERROR.items() if isinstance(error, t)), 500)


def error_body(error: DomainError) -> dict[str, object]:
    """Cuerpo de error (puro). Los errores con espera exponen `retry_after_s`."""
    body: dict[str, object] = {"error": error.code}
    retry_after = getattr(error, "retry_after_s", None)
    if retry_after is not None:
        body["retry_after_s"] = retry_after
    elif error.detail and error.detail != error.code:
        body["detail"] = error.detail
    return body


def error_headers(error: DomainError) -> dict[str, str]:
    headers: dict[str, str] = {}
    retry_after = getattr(error, "retry_after_s", None)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    elif isinstance(error, OverloadedError):
        headers["Retry-After"] = "1"
    if isinstance(error, UnauthorizedError):
        headers["WWW-Authenticate"] = "ApiKey"
    if isinstance(error, RateLimitExceededError):
        headers["X-RateLimit-Limit"] = str(error.limit)
        headers["X-RateLimit-Remaining"] = "0"
    return headers


async def _handle_domain_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)  # noqa: S101 — registrado sólo para DomainError
    return JSONResponse(error_body(exc), status_code=status_for(exc), headers=error_headers(exc))


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors = [
        {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()
    ]
    return JSONResponse({"error": "invalid_request", "detail": errors}, status_code=422)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
