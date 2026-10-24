"""Carga open-loop desde *dentro* de la red de Docker (sin el port-forward del host).

Separa la latencia del sistema de la del entorno local (Docker Desktop / colima reenvían el
puerto del host a la VM con un proxy que agrega cientos de ms bajo carga).

Uso: `make load-internal` (copia este script a una réplica y lo ejecuta contra nginx).
"""

from __future__ import annotations

import asyncio
import random
import sys
import time

import httpx

HEADERS = {"X-API-Key": "demo-key-123"}
POPULAR = [f"¿Qué es la arquitectura número {i}?" for i in range(20)]
TOTAL, INTERVAL_S = 1500, 0.01  # ~100 RPS durante 15 s


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, int(len(ordered) * p) - 1)]


async def one(client: httpx.AsyncClient, kind: str, lat: dict[str, list[float]]) -> int:
    started = time.perf_counter()
    if kind == "classify":
        response = await client.post("/v1/classify", json={"input": "me encantó"}, headers=HEADERS)
    else:
        body = {"model": "gpt-4o", "input": random.choice(POPULAR)}
        response = await client.post("/v1/infer", json=body, headers=HEADERS)
    lat[kind].append((time.perf_counter() - started) * 1000)
    return response.status_code


async def main(base_url: str) -> None:
    lat: dict[str, list[float]] = {"classify": [], "infer": []}
    limits = httpx.Limits(max_connections=200)
    async with httpx.AsyncClient(base_url=base_url, limits=limits) as client:
        started = time.perf_counter()
        tasks = []
        for i in range(TOTAL):
            kind = "classify" if i % 2 else "infer"
            tasks.append(asyncio.create_task(one(client, kind, lat)))
            await asyncio.sleep(INTERVAL_S)
        codes = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - started
    errors = sum(code != 200 for code in codes)
    print(f"rps={TOTAL / elapsed:.0f} errores={errors}/{TOTAL}")  # noqa: T201
    for kind, values in lat.items():
        p50, p95, p99 = (percentile(values, p) for p in (0.5, 0.95, 0.99))
        print(f"{kind}: p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://nginx"))
