"""Errores de dominio tipados.

El dominio nunca conoce HTTP: lanza estas excepciones y `app/api/errors.py` las traduce a
status codes en un solo lugar. Cada error lleva un `code` estable que es parte del contrato público.
"""

from __future__ import annotations


class DomainError(Exception):
    code: str = "internal_error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.code)
        self.detail = detail


class UnauthorizedError(DomainError):
    code = "unauthorized"


class RateLimitExceededError(DomainError):
    code = "rate_limit_exceeded"

    def __init__(self, retry_after_s: int, limit: int) -> None:
        super().__init__(f"Límite de {limit} requests por ventana excedido")
        self.retry_after_s = retry_after_s
        self.limit = limit


class UnknownModelError(DomainError):
    code = "unknown_model"


class UnknownModelVersionError(DomainError):
    code = "unknown_model_version"


class OverloadedError(DomainError):
    """La réplica no tiene capacidad (bulkhead lleno o load shedding)."""

    code = "overloaded"


class LLMUnavailableError(DomainError):
    """Circuito abierto: se falla sin llamar al proveedor."""

    code = "llm_unavailable"

    def __init__(self, retry_after_s: int) -> None:
        super().__init__("El proveedor LLM no está disponible temporalmente")
        self.retry_after_s = retry_after_s


class LLMTimeoutError(DomainError):
    code = "llm_timeout"


class LLMProviderError(DomainError):
    """Falla del proveedor.

    `retryable`: tiene sentido reintentar (5xx, errores de red).
    `counts_as_failure`: indica salud del proveedor y alimenta el circuit breaker (no los 4xx).
    """

    code = "llm_error"

    def __init__(self, detail: str, *, retryable: bool, counts_as_failure: bool) -> None:
        super().__init__(detail)
        self.retryable = retryable
        self.counts_as_failure = counts_as_failure


class SlowConsumerError(DomainError):
    code = "slow_consumer"


class BackendUnavailableError(Exception):
    """Una dependencia de infraestructura (Redis) no respondió a tiempo o está en cooldown.

    No es un error de dominio: los adaptadores lo capturan y aplican su fallback.
    """
