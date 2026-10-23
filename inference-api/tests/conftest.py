from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import fakeredis
import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from redis.asyncio import Redis

from app.config import Settings
from app.container import Container
from app.core.clock import ManualClock, SystemClock
from app.core.observability.metrics import Metrics
from app.core.ports import LLMProvider
from app.core.redis_gateway import RedisGateway
from app.main import create_app
from tests.fakes import ScriptedProvider

API_KEY = "demo-key-123"
AUTH = {"X-API-Key": API_KEY}


@pytest.fixture
def settings() -> Settings:
    return Settings(log_level="WARNING", llm_retry_base_delay_s=0.001)


@pytest.fixture
def redis_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
def redis_client(redis_server: fakeredis.FakeServer) -> Redis:
    return fakeredis.FakeAsyncRedis(server=redis_server)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def gateway(redis_client: Redis, clock: ManualClock) -> RedisGateway:
    return RedisGateway(redis_client, clock)


@pytest.fixture
def provider() -> ScriptedProvider:
    return ScriptedProvider()


AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(settings: Settings, redis_client: Redis, provider: ScriptedProvider) -> AppFactory:
    def factory(
        *,
        settings_override: Settings | None = None,
        llm: LLMProvider | None = None,
        redis: Redis | None = None,
    ) -> FastAPI:
        async def build(s: Settings, metrics: Metrics) -> Container:
            return await Container.build(
                s,
                metrics=metrics,
                clock=SystemClock(),
                redis_client=redis or redis_client,
                provider=llm or provider,
            )

        return create_app(settings_override or settings, container_factory=build)

    return factory


@pytest.fixture
async def client(make_app: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(make_app()) as http:
        yield http


def serve(app: FastAPI) -> _Served:
    return _Served(app)


class _Served:
    """`async with serve(app) as http:` levanta lifespan y devuelve un cliente httpx."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app
        self._manager = LifespanManager(app)
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        await self._manager.__aenter__()
        transport = httpx.ASGITransport(app=self._manager.app)
        self._http = httpx.AsyncClient(transport=transport, base_url="http://test")
        return self._http

    async def __aexit__(self, *exc: object) -> None:
        assert self._http is not None
        await self._http.aclose()
        await self._manager.__aexit__(*exc)
