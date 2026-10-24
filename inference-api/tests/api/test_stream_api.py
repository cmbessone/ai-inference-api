"""Contrato HTTP de `POST /v1/infer/stream` (R2)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI
from redis.asyncio import Redis

from tests.conftest import AUTH, AppFactory
from tests.fakes import ScriptedProvider

BODY = {"model": "gpt-4o", "input": "Hola"}


async def test_stream_contract(client: httpx.AsyncClient) -> None:
    """R2-AC01 + R2-AC06."""
    response = await client.post("/v1/infer/stream", json=BODY, headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-ratelimit-limit"] == "60"
    assert response.text == (
        'data: {"token": "Hola", "index": 0}\n\n'
        'data: {"token": " mundo", "index": 1}\n\n'
        "data: [DONE]\n\n"
    )


async def test_stream_requires_api_key(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/infer/stream", json=BODY)
    assert response.status_code == 401


async def test_stream_is_audited_in_redis(client: httpx.AsyncClient, redis_client: Redis) -> None:
    """R2-AC05: el worker en background escribe el stream completo en Redis Streams."""
    await client.post("/v1/infer/stream", json=BODY, headers=AUTH)
    for _ in range(50):
        entries = await redis_client.xrange("audit:streams")
        if entries:
            break
        await asyncio.sleep(0.01)
    fields = entries[0][1]
    assert fields[b"output"] == b"Hola mundo"
    assert fields[b"client_id"] == b"demo-client"
    assert fields[b"end_reason"] == b"completed"


@contextlib.asynccontextmanager
async def running_server(app: FastAPI) -> AsyncIterator[str]:
    """Uvicorn real en un puerto efímero: necesario para probar desconexiones TCP de verdad."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 — uvicorn no expone un Event de arranque
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def test_client_disconnect_cancels_upstream_end_to_end(make_app: AppFactory) -> None:
    """R2-AC03 con un socket real: el cliente corta y el proveedor deja de generar."""
    provider = ScriptedProvider(tokens=tuple(f"t{i}" for i in range(500)), token_delay_s=0.01)
    app = make_app(llm=provider)
    async with running_server(app) as base_url, httpx.AsyncClient(base_url=base_url) as http:
        async with http.stream("POST", "/v1/infer/stream", json=BODY, headers=AUTH) as response:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    break  # leemos un token y cortamos la conexión
        await asyncio.wait_for(provider.stream_closed.wait(), timeout=2)
    assert provider.stream_closed.is_set()
