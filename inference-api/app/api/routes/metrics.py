"""`GET /metrics` en formato Prometheus (OB-AC07)."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Header, Response
from prometheus_client import CONTENT_TYPE_LATEST

from app.api.dependencies import ContainerDep
from app.core.errors import UnauthorizedError

router = APIRouter(tags=["observability"])


def is_authorized(expected: str | None, authorization: str | None) -> bool:
    """Sin token configurado, abierto (local/red interna); con token, Bearer exacto (pura)."""
    if expected is None:
        return True
    return hmac.compare_digest(authorization or "", f"Bearer {expected}")


@router.get("/metrics", include_in_schema=False)
async def metrics(
    container: ContainerDep, authorization: Annotated[str | None, Header()] = None
) -> Response:
    token = container.settings.metrics_token
    if not is_authorized(token.get_secret_value() if token else None, authorization):
        raise UnauthorizedError("Token de métricas ausente o inválido")
    await container.llm.circuit_state()  # refresca el gauge con el estado compartido actual
    return Response(container.metrics.render(), media_type=CONTENT_TYPE_LATEST)
