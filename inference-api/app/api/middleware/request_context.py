"""Middleware ASGI de contexto por request: request_id, log de acceso y métricas.

Es ASGI puro (no `BaseHTTPMiddleware`) a propósito: `BaseHTTPMiddleware` envuelve el body en
memoria y rompe la cancelación/backpressure de las respuestas en streaming (SSE).
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, Protocol

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.clock import Clock, SystemClock
from app.core.observability.logging import get_logger

Outcome = Literal["success", "error", "timeout"]

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_SILENT_PATHS = frozenset({"/health/live", "/health/ready", "/metrics"})

logger = get_logger("access")


@dataclass(slots=True)
class RequestContext:
    """Acumulador mutable del request (cáscara imperativa).

    Las rutas completan los campos que sólo ellas conocen (modelo, cache hit, motivo de fin);
    el middleware los lee al final para emitir una única línea de log de acceso (OB-AC01).
    """

    request_id: str
    client_id: str | None = None
    model: str | None = None
    cache_hit: bool | None = None
    outcome: Outcome | None = None


_current: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def current_context() -> RequestContext | None:
    return _current.get()


# ── Funciones puras ─────────────────────────────────────────────────────────────


def resolve_request_id(incoming: str | None) -> str:
    """Reusa el `X-Request-ID` entrante si es seguro; si no, genera un UUID4 (OB-AC02)."""
    if incoming and _REQUEST_ID_PATTERN.fullmatch(incoming):
        return incoming
    return str(uuid.uuid4())


def classify_outcome(status_code: int, override: Outcome | None) -> Outcome:
    """Traduce el status HTTP al campo `status` del log, respetando lo que decidió la ruta."""
    if override is not None:
        return override
    if status_code == 504:
        return "timeout"
    return "success" if status_code < 400 else "error"


def header_value(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return str(value.decode("latin-1"))
    return None


# ── Middleware ──────────────────────────────────────────────────────────────────


class RequestObserver(Protocol):
    """Hook para métricas: el middleware no conoce Prometheus (inversión de dependencias)."""

    def observe(self, method: str, route: str, status_code: int, duration_s: float) -> None: ...


class RequestContextMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        observer: RequestObserver | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._app = app
        self._observer = observer
        self._clock = clock or SystemClock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        ctx = RequestContext(request_id=resolve_request_id(header_value(scope, b"x-request-id")))
        token = _current.set(ctx)
        structlog.contextvars.bind_contextvars(request_id=ctx.request_id)
        started = self._clock.monotonic()
        status_code = 500
        logged = False

        def finish() -> None:
            nonlocal logged
            if logged:
                return
            logged = True
            self._record(scope, ctx, status_code, self._clock.monotonic() - started)

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", ctx.request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finish()

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            finish()  # cubre excepciones y desconexiones a mitad de stream
            structlog.contextvars.unbind_contextvars("request_id")
            _current.reset(token)

    def _record(self, scope: Scope, ctx: RequestContext, status_code: int, elapsed: float) -> None:
        path: str = scope.get("path", "")
        route = scope.get("route")
        route_path: str = getattr(route, "path", path)
        if self._observer is not None:
            self._observer.observe(scope.get("method", ""), route_path, status_code, elapsed)
        if path in _SILENT_PATHS:
            return
        logger.info(
            "request",
            request_id=ctx.request_id,
            client_id=ctx.client_id,
            model=ctx.model,
            method=scope.get("method"),
            path=path,
            status_code=status_code,
            latency_ms=round(elapsed * 1000, 2),
            cache_hit=ctx.cache_hit,
            status=classify_outcome(status_code, ctx.outcome),
        )
