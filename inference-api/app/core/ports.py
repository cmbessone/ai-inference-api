"""Puertos (arquitectura hexagonal).

El dominio y los servicios dependen de estos `Protocol`s; los adaptadores concretos (Redis,
OpenAI, mock, memoria) los implementan. Tipado estructural: no hace falta heredar.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LLMRequest:
    model: str
    prompt: str
    temperature: float = 0.7
    max_tokens: int = 256

    def as_cache_config(self) -> Mapping[str, object]:
        """Parámetros que afectan la salida y por lo tanto forman parte de la clave de cache."""
        return {"temperature": self.temperature, "max_tokens": self.max_tokens}


@dataclass(frozen=True, slots=True)
class Usage:
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    usage: Usage


@dataclass(frozen=True, slots=True)
class StreamSummary:
    """Resultado de un stream completo (para auditoría)."""

    tokens: tuple[str, ...] = field(default_factory=tuple)

    @property
    def text(self) -> str:
        return "".join(self.tokens)


class LLMProvider(Protocol):
    """Adaptador de un proveedor de LLM (Strategy)."""

    name: str

    async def complete(self, request: LLMRequest) -> Completion: ...

    def stream(self, request: LLMRequest) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


class Cache(Protocol):
    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, ttl_s: float) -> None: ...


class DistributedLock(Protocol):
    async def acquire(self, key: str, token: str, ttl_s: float) -> bool: ...

    async def release(self, key: str, token: str) -> None: ...
