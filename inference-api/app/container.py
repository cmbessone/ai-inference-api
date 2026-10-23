"""Composition root.

Único lugar donde se instancian adaptadores concretos y se conectan entre sí. El resto del código
depende de puertos (`Protocol`s) y recibe sus dependencias inyectadas: eso permite reemplazar
Redis, el proveedor LLM o el runtime de ML sin tocar la lógica, y testear con dobles.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings
from app.core.clock import Clock, SystemClock


@dataclass
class Lifecycle:
    """Estado de ciclo de vida de la réplica, consultado por `/health/ready`."""

    ready: bool = False
    shutting_down: bool = False


@dataclass
class Container:
    settings: Settings
    clock: Clock = field(default_factory=SystemClock)
    lifecycle: Lifecycle = field(default_factory=Lifecycle)

    @classmethod
    async def build(cls, settings: Settings) -> Container:
        return cls(settings=settings)

    async def start(self) -> None:
        self.lifecycle.ready = True

    async def aclose(self) -> None:
        self.lifecycle.shutting_down = True
        self.lifecycle.ready = False
