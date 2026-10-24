"""Punto de entrada: `create_app()` (Factory) + `lifespan`.

El factory permite construir la app con otra configuración o con un container de pruebas sin
variables globales, y el `lifespan` concentra arranque (carga de modelos, conexiones) y apagado
ordenado (drenar colas, cerrar pools).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes import health
from app.config import Settings, get_settings
from app.container import Container
from app.core.observability.logging import configure_logging, get_logger

logger = get_logger(__name__)


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active = container or await Container.build(settings)
        app.state.container = active
        await active.start()
        logger.info("startup_complete", service=settings.service_name, env=settings.environment)
        try:
            yield
        finally:
            await active.aclose()
            logger.info("shutdown_complete")

    app = FastAPI(
        title="AI Inference API",
        version="1.0.0",
        description="Inferencia LLM (sync + SSE) y clasificación con modelo propio.",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.add_middleware(RequestContextMiddleware)
    return app
