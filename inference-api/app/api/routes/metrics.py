"""`GET /metrics` en formato Prometheus (OB-AC07)."""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST

from app.api.dependencies import ContainerDep

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def metrics(container: ContainerDep) -> Response:
    await container.llm.circuit_state()  # refresca el gauge con el estado compartido actual
    return Response(container.metrics.render(), media_type=CONTENT_TYPE_LATEST)
