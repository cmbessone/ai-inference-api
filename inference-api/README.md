# AI Inference API

API de inferencia **escalable y de alta disponibilidad** para una plataforma SaaS: LLM
sincrónico con cache, streaming SSE resiliente y clasificación con un modelo propio versionado.

Construida con **Spec-Driven Development**: las [specs](specs/README.md) definen criterios de
aceptación numerados, cada uno con su test (`make trace` → 56/56). Las decisiones y trade-offs
están en **[DECISIONS.md](DECISIONS.md)** y la arquitectura en
**[docs/architecture/c4.md](docs/architecture/c4.md)**.

## Quickstart

```bash
docker compose up --build        # nginx :8080 → 3 réplicas → redis (sin configuración extra)
open http://localhost:8080/docs  # OpenAPI
```

> Si tu Docker no trae el plugin `compose`, usá `docker-compose` (el Makefile lo detecta solo).

API keys de demo: `demo-key-123` (cliente `demo-client`) y `acme-key-456` (cliente `acme`).

## Endpoints

### `POST /v1/infer` — inferencia sincrónica (R1)

```bash
curl -s localhost:8080/v1/infer \
  -H 'X-API-Key: demo-key-123' -H 'Content-Type: application/json' \
  -d '{"model": "gpt-4o", "input": "Hola mundo", "config": {"temperature": 0.7}}'
```

```json
{"request_id": "d2585d60…", "output": "Respuesta simulada de gpt-4o: mundo Hola",
 "model": "gpt-4o", "usage": {"tokens_in": 2, "tokens_out": 6}, "latency_ms": 64, "cache_hit": false}
```

Repetir el mismo request devuelve `"cache_hit": true`. Headers: `X-Request-ID`,
`X-RateLimit-Limit`, `X-RateLimit-Remaining`. Errores:

| Status | Body | Cuándo |
|---|---|---|
| 401 | `{"error": "unauthorized"}` | Falta o es inválida la `X-API-Key` |
| 429 | `{"error": "rate_limit_exceeded", "retry_after_s": 12}` | Más de 60 req/min por cliente |
| 400 | `{"error": "unknown_model", …}` | Modelo no permitido |
| 503 | `{"error": "llm_unavailable", "retry_after_s": 45}` | Circuito abierto |
| 503 | `{"error": "overloaded"}` | Réplica saturada (load shedding) |
| 504 | `{"error": "llm_timeout"}` | Se excedió `timeout_s` (default 30) |

### `POST /v1/infer/stream` — streaming SSE (R2)

```bash
curl -N localhost:8080/v1/infer/stream \
  -H 'X-API-Key: demo-key-123' -H 'Content-Type: application/json' \
  -d '{"model": "gpt-4o", "input": "Hola mundo", "timeout_s": 30}'
```

```
data: {"token": "Respuesta", "index": 0}

data: {"token": " simulada", "index": 1}
…
data: [DONE]
```

Con el circuito abierto: `data: {"error": "llm_unavailable", "retry_after_s": 45}`.

### `POST /v1/classify` — modelo propio (R3)

```bash
curl -s localhost:8080/v1/classify \
  -H 'X-API-Key: demo-key-123' -H 'X-Model-Version: v2' -H 'Content-Type: application/json' \
  -d '{"input": "excelente servicio, me encantó"}'
```

```json
{"label": "positive", "confidence": 0.94, "model_version": "v2", "latency_ms": 12}
```

`X-Model-Version: v9` → `400 {"error": "unknown_model_version", "detail": "Versión 'v9' no existe. Disponibles: v1, v2"}`.

### Operación

| Endpoint | Uso |
|---|---|
| `GET /health` | `{"status": "ok", "redis": "ok", "llm_circuit": "closed", "models_loaded": ["v1", "v2"]}` |
| `GET /health/live` | Liveness (no toca dependencias) |
| `GET /health/ready` | Readiness (503 al arrancar y durante el shutdown) |
| `GET /metrics` | Prometheus |

Colección **Bruno** con todos los requests en [`bruno/`](bruno/) (abrir la carpeta en Bruno y
elegir el entorno `local`).

## Comandos

```bash
make install         # dependencias con uv
make lint            # ruff + mypy --strict
make test            # 110 tests (~2 s, sin Docker)
make trace           # matriz criterio → test (falla si falta alguno)
make up / make down  # stack completo
make chaos           # mata una réplica bajo tráfico (demo de HA)
make up-load && make load          # locust a ~100 RPS desde el host
make load-internal                 # carga desde la red de Docker
uv run pytest -m integration       # pruebas contra el stack levantado
```

## Simular fallas

```bash
MOCK_FAILURE_RATE=1 docker compose up -d   # el LLM falla siempre → el circuito abre al 5.º fallo
docker compose stop redis                  # la API degrada (cuota local, sin cache) pero responde
LLM_PROVIDER=openai OPENAI_API_KEY=sk-… docker compose up -d   # proveedor real
```

## Estructura

```
inference-api/
├── app/
│   ├── main.py                  # create_app() + lifespan
│   ├── config.py                # Settings inmutables (env vars)
│   ├── container.py             # composition root
│   ├── api/
│   │   ├── routes/              # infer.py, stream.py, classify.py, health.py, metrics.py
│   │   └── middleware/          # auth.py, rate_limit.py, request_context.py, load_shedding.py
│   ├── services/                # inference.py, streaming.py, classification.py
│   └── core/
│       ├── llm_client.py        # abstracción + circuit breaker (Decorator)
│       ├── ml_model.py          # carga + versioning + micro-batching
│       ├── cache.py             # redis wrapper + lock distribuido
│       ├── circuit_breaker.py   # máquina de estados pura + stores
│       ├── rate_limiter.py      # sliding log (Lua) + fallback
│       ├── single_flight.py · redis_gateway.py · audit.py · sse.py · ports.py
│       └── providers/           # mock, openai
├── ml/                          # dataset + entrenamiento v1/v2 (corre en el build)
├── tests/                       # unit · api · integration
├── specs/                       # SDD: constitución, specs, plan, tareas, trazabilidad
├── docs/architecture/           # C4 (Mermaid + Structurizr DSL)
├── deploy/                      # nginx, prometheus, override de carga
├── loadtest/                    # locust + resultados
├── docker-compose.yml · Dockerfile · Makefile
└── DECISIONS.md                 # ⭐ arquitectura, trade-offs, escenarios A/B/C
```
