"""Load shedding por réplica (RS-AC10).

Si la réplica ya tiene `max_inflight` requests en curso, rechaza los nuevos con 503 **antes**
de hacer trabajo. Ante un pico, responder rápido "probá en 1 s" es mejor que encolar
indefinidamente y que *todos* los requests superen el SLA (y los healthchecks fallen en cascada).
El balanceador / cliente reintenta en otra réplica.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.observability.metrics import Metrics

_EXEMPT_PREFIXES = ("/health", "/metrics")
_BODY = json.dumps({"error": "overloaded"}).encode()


def is_exempt(path: str) -> bool:
    """Health y métricas nunca se descartan: el orquestador debe poder ver a la réplica."""
    return path.startswith(_EXEMPT_PREFIXES)


class LoadSheddingMiddleware:
    def __init__(self, app: ASGIApp, max_inflight: int, metrics: Metrics) -> None:
        self._app = app
        self._max_inflight = max_inflight
        self._metrics = metrics
        self._inflight = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_exempt(scope.get("path", "")):
            await self._app(scope, receive, send)
            return
        if self._inflight >= self._max_inflight:
            self._metrics.load_shed.inc()
            await _reject(send)
            return
        self._inflight += 1
        self._metrics.inflight.inc()
        try:
            await self._app(scope, receive, send)
        finally:
            self._inflight -= 1
            self._metrics.inflight.dec()


async def _reject(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"retry-after", b"1"),
                (b"content-length", str(len(_BODY)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _BODY})
