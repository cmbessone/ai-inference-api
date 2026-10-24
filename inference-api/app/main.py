"""Punto de entrada: `create_app()` (Factory) + `lifespan`.

El factory permite construir la app con otra configuración o con un container de pruebas sin
variables globales, y el `lifespan` concentra arranque (carga de modelos, conexiones) y apagado
ordenado (drenar colas, cerrar pools).

Ejecutar con: `uvicorn app.main:create_app --factory`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from app.api.errors import register_error_handlers
from app.api.middleware.load_shedding import LoadSheddingMiddleware
from app.api.middleware.rate_limit import enforce_rate_limit
from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes import classify, health, infer, metrics, stream
from app.config import Settings, get_settings
from app.container import Container
from app.core.observability.logging import configure_logging, get_logger
from app.core.observability.metrics import Metrics

logger = get_logger(__name__)

ContainerFactory = Callable[[Settings, Metrics], Awaitable[Container]]


async def _default_factory(settings: Settings, metrics: Metrics) -> Container:
    return await Container.build(settings, metrics=metrics)


def build_v1_router() -> APIRouter:
    """Todo `/v1/*` exige API key + rate limit (la auth es dependencia del rate limit)."""
    router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])
    router.include_router(infer.router)
    router.include_router(stream.router)
    router.include_router(classify.router)
    return router


def create_app(
    settings: Settings | None = None,
    container_factory: ContainerFactory = _default_factory,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    registry = Metrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await container_factory(settings, registry)
        app.state.container = container
        await container.start()
        logger.info("startup_complete", service=settings.service_name, env=settings.environment)
        try:
            yield
        finally:
            await container.aclose()
            logger.info("shutdown_complete")

    app = FastAPI(
        title="AI Inference API",
        version="1.0.0",
        description="Inferencia LLM (sync + SSE) y clasificación con modelo propio.",
        lifespan=lifespan,
    )
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(build_v1_router())
    # El último agregado es el más externo: el contexto/log envuelve también a los rechazos 503.
    app.add_middleware(
        LoadSheddingMiddleware, max_inflight=settings.max_inflight_requests, metrics=registry
    )
    app.add_middleware(RequestContextMiddleware, observer=registry)
    return app
