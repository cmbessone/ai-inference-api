"""Autenticación por API key (`X-API-Key`).

Se implementa como **dependencia de router** y no como middleware ASGI (ADR-006):
- aplica sólo a `/v1/*` (health y metrics quedan abiertos para el orquestador);
- aparece en OpenAPI como esquema de seguridad;
- se reemplaza en tests con `dependency_overrides`;
- no interfiere con el streaming (los `BaseHTTPMiddleware` sí).

La resolución key → cliente vive en `app/core/auth.py` (puerto `KeyStore`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from app.api.dependencies import ContainerDep
from app.core.errors import UnauthorizedError
from app.core.observability.context import current_context

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    container: ContainerDep,
    api_key: Annotated[str | None, Security(_api_key_header)],
) -> str:
    client_id = container.key_store.client_for(api_key) if api_key else None
    if client_id is None:
        raise UnauthorizedError("API key ausente o inválida")
    if (ctx := current_context()) is not None:
        ctx.client_id = client_id
    return client_id


ClientId = Annotated[str, Depends(require_api_key)]
