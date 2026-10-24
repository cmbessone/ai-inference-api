"""Prueba de carga (OB-AC08): mezcla realista de tráfico a ~100 RPS contra el stack completo.

Uso: `make up-load && make load` (el override sube el rate limit: acá se mide capacidad, no cuota).

Mezcla:
- 50 % clasificación (CPU, modelo propio)
- 35 % inferencia con prompts populares (→ cache hits, como en producción)
- 10 % inferencia con prompts únicos (→ cache miss, llama al LLM)
-  5 % streaming SSE
"""

from __future__ import annotations

import random
import uuid

from locust import FastHttpUser, constant_throughput, task

HEADERS = {"X-API-Key": "demo-key-123", "Content-Type": "application/json"}
POPULAR = [f"¿Qué es la arquitectura número {i}?" for i in range(20)]
REVIEWS = [
    "me encantó el producto, excelente",
    "pésima atención, no lo recomiendo",
    "el pedido llegó el martes",
    "great quality, very happy",
]


class ApiUser(FastHttpUser):
    # Cada usuario hace ~1 request/s: 100 usuarios ≈ 100 RPS.
    wait_time = constant_throughput(1)

    @task(50)
    def classify(self) -> None:
        version = random.choice(["v1", "v2"])
        self.client.post(
            "/v1/classify",
            json={"input": random.choice(REVIEWS)},
            headers={**HEADERS, "X-Model-Version": version},
            name="/v1/classify",
        )

    @task(35)
    def infer_cached(self) -> None:
        self.client.post(
            "/v1/infer",
            json={"model": "gpt-4o", "input": random.choice(POPULAR)},
            headers=HEADERS,
            name="/v1/infer [popular]",
        )

    @task(10)
    def infer_unique(self) -> None:
        self.client.post(
            "/v1/infer",
            json={"model": "gpt-4o", "input": f"pregunta única {uuid.uuid4()}"},
            headers=HEADERS,
            name="/v1/infer [unique]",
        )

    @task(5)
    def stream(self) -> None:
        with self.client.post(
            "/v1/infer/stream",
            json={"model": "gpt-4o", "input": "contame algo corto"},
            headers=HEADERS,
            name="/v1/infer/stream",
            stream=True,
            catch_response=True,
        ) as response:
            body = response.content or b""
            if b"[DONE]" not in body:
                response.failure("stream sin [DONE]")
