"""`POST /v1/infer/stream` — streaming SSE (R2)."""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import StreamingResponse

from app.api.dependencies import ContainerDep
from app.api.middleware.auth import ClientId
from app.api.routes.infer import ensure_model_allowed, resolve_timeout
from app.api.schemas import InferRequest
from app.core.observability.context import current_context
from app.services.streaming import StreamIdentity

router = APIRouter(tags=["inference"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # nginx: no bufferear, reenviar cada evento al instante
    "Connection": "keep-alive",
}


@router.post(
    "/infer/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream(
    body: InferRequest, client_id: ClientId, container: ContainerDep, response: Response
) -> StreamingResponse:
    ensure_model_allowed(body.model, container)
    ctx = current_context()
    if ctx is not None:
        ctx.model = body.model
        ctx.cache_hit = False

    settings = container.settings
    events = container.streaming.events(
        body.to_domain(),
        timeout_s=resolve_timeout(
            body.timeout_s, settings.llm_default_timeout_s, settings.llm_max_timeout_s
        ),
        identity=StreamIdentity(request_id=ctx.request_id if ctx else "", client_id=client_id),
    )
    # Al devolver una Response propia, FastAPI no copia los headers de las dependencias
    # (X-RateLimit-*): se trasladan explícitamente.
    headers = {**SSE_HEADERS, **forwarded_headers(response)}
    return StreamingResponse(events, media_type="text/event-stream", headers=headers)


def forwarded_headers(response: Response) -> dict[str, str]:
    return {k: v for k, v in response.headers.items() if k.lower().startswith("x-ratelimit-")}
