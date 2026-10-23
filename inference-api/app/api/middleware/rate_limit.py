"""Rate limiting por cliente como dependencia de router.

Corre **después** de la autenticación (necesita el `client_id`), así un request con key inválida
nunca consume cuota de nadie (R1-AC02).
"""

from __future__ import annotations

from fastapi import Response

from app.api.dependencies import ContainerDep
from app.api.middleware.auth import ClientId
from app.core.errors import RateLimitExceededError


async def enforce_rate_limit(
    client_id: ClientId, container: ContainerDep, response: Response
) -> None:
    decision = await container.rate_limiter.hit(client_id)
    if not decision.allowed:
        container.metrics.rate_limited.inc()
        raise RateLimitExceededError(retry_after_s=decision.retry_after_s, limit=decision.limit)
    response.headers["X-RateLimit-Limit"] = str(decision.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
