"""Reloj inyectable.

Las funciones de dominio reciben `now` como argumento en lugar de leer el reloj: eso las vuelve
puras y deterministas en tests. Los adaptadores reciben un `Clock` para obtenerlo.
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Segundos desde epoch (pared). Se usa para ventanas compartidas entre réplicas."""
        ...

    def monotonic(self) -> float:
        """Segundos monotónicos. Se usa para medir duraciones dentro del proceso."""
        ...


class SystemClock:
    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()


class ManualClock:
    """Reloj controlable para tests."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds
