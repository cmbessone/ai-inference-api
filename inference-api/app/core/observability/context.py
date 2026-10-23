"""Contexto por request compartido entre capas (contextvars).

Vive en `core` (no en `api`) para que los servicios puedan anotar el resultado del request
(modelo, cache hit, motivo de fin) sin depender de la capa HTTP.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Literal

Outcome = Literal["success", "error", "timeout"]


@dataclass(slots=True)
class RequestContext:
    """Acumulador mutable del request (cáscara imperativa).

    Las rutas y servicios completan los campos que sólo ellos conocen; el middleware los lee al
    final para emitir una única línea de log de acceso (OB-AC01). asyncio copia el contexto a
    cada tarea hija, pero el objeto es el mismo: las mutaciones son visibles para el middleware.
    """

    request_id: str
    client_id: str | None = None
    model: str | None = None
    cache_hit: bool | None = None
    outcome: Outcome | None = None


_current: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def current_context() -> RequestContext | None:
    return _current.get()


def bind_context(ctx: RequestContext) -> Token[RequestContext | None]:
    return _current.set(ctx)


def reset_context(token: Token[RequestContext | None]) -> None:
    _current.reset(token)
