"""Single-flight local: N llamadas concurrentes con la misma clave → 1 ejecución.

La ejecución corre en una tarea propia, desacoplada de quien la disparó: si el primer cliente se
desconecta, los demás siguen esperando el resultado y el cache se completa igual. Es la primera
capa contra el thundering herd (Escenario A); la segunda es el lock distribuido en Redis.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial


@dataclass(frozen=True, slots=True)
class Shared[T]:
    value: T
    shared: bool  # True si este llamador reutilizó una ejecución iniciada por otro


class SingleFlight[T]:
    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task[T]] = {}

    def inflight(self) -> int:
        return len(self._inflight)

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> Shared[T]:
        task = self._inflight.get(key)
        shared = task is not None
        if task is None:
            task = asyncio.ensure_future(fn())
            self._inflight[key] = task
            task.add_done_callback(partial(self._forget, key))
        # `shield`: cancelar a un llamador no cancela la ejecución compartida.
        value = await asyncio.shield(task)
        return Shared(value=value, shared=shared)

    def _forget(self, key: str, task: asyncio.Task[T]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            task.exception()  # marca la excepción como recuperada aunque nadie espere
