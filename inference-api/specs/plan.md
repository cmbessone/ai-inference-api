# Plan técnico

> Traduce las specs `001`–`005` a componentes. Las razones de cada decisión están en `DECISIONS.md`.

## Capas (puertos y adaptadores)

```
app/
├── main.py                 # create_app() + lifespan  (Factory)
├── config.py               # Settings inmutables (pydantic-settings)
├── container.py            # Composition root: arma el grafo de dependencias
├── api/                    # ── Adaptadores de entrada (HTTP)
│   ├── schemas.py          #    DTOs Pydantic (contrato público)
│   ├── errors.py           #    Errores de dominio → HTTP (un solo lugar)
│   ├── dependencies.py     #    Providers de FastAPI Depends
│   ├── middleware/         #    auth, rate_limit (dependencias), request_context, load_shedding (ASGI)
│   └── routes/             #    infer, stream, classify, health, metrics
├── services/               # ── Casos de uso (orquestación)
│   ├── inference.py        #    cache-aside + single-flight + LLM resiliente
│   ├── streaming.py        #    relay con buffer acotado + auditoría
│   └── classification.py   #    registry + batcher + thread pool
└── core/                   # ── Dominio + puertos + adaptadores de salida
    ├── ports.py            #    Protocols: LLMProvider, Cache, RateLimiter, BreakerStore, AuditSink, …
    ├── errors.py           #    Excepciones de dominio tipadas
    ├── clock.py            #    Reloj inyectable
    ├── cache.py            #    cache_key() pura + RedisCache
    ├── rate_limiter.py     #    ventana deslizante pura + Redis (Lua) + fallback local
    ├── circuit_breaker.py  #    máquina de estados pura + stores Redis/memoria
    ├── single_flight.py    #    coalescing local + lock distribuido
    ├── redis_gateway.py    #    Redis con timeout + breaker local (fail fast)
    ├── llm_client.py       #    ResilientLLMClient (Decorator: breaker, timeout, bulkhead, retry)
    ├── providers/          #    mock (default), openai (opcional)  (Strategy + Factory)
    ├── ml_model.py         #    ModelRegistry, carga verificada, MicroBatcher
    ├── audit.py            #    AuditSink con cola acotada + worker
    ├── sse.py              #    formateo SSE puro
    └── observability/      #    logging JSON, métricas Prometheus
```

## Flujo `POST /v1/infer`

1. `LoadShedding` (ASGI) → `RequestContext` (ASGI: request_id, log de acceso, métricas)
2. `require_api_key` → `client_id`
3. `enforce_rate_limit` → Lua atómico en Redis (fallback local)
4. `InferenceService.infer`:
   1. `cache_key(model, input, config)` (pura)
   2. cache **hit** → responder
   3. cache **miss** → `SingleFlight` local (1 tarea por clave por réplica)
      → lock distribuido `SET NX PX` (1 tarea por clave en el clúster)
      → *líder*: re-chequea cache, llama `ResilientLLMClient`, escribe cache, libera lock
      → *seguidores*: esperan el valor en cache con backoff; si expira, llaman ellos (degradación)
5. Respuesta con `usage`, `latency_ms`, `cache_hit`

## Flujo `POST /v1/infer/stream`

1. Igual que arriba hasta el rate limit.
2. `ResilientLLMClient.stream` → si el circuito está abierto, evento de error inmediato.
3. Tarea **productora** lee tokens del proveedor → `asyncio.Queue(maxsize=N)`.
4. Generador **consumidor** (StreamingResponse) saca tokens → `format_sse()` → socket.
5. Desconexión / timeout / slow consumer → se cancela la productora → se cierra el stream HTTP upstream.
6. `finally`: `AuditSink.submit(record)` (no bloqueante).

## Flujo `POST /v1/classify`

1. Auth + rate limit.
2. `resolve_version(header, registry)` (pura) → 400 si no existe.
3. `MicroBatcher[version].submit(text)` → ventana 10 ms → `predict_proba(batch)` en thread pool acotado.

## Despliegue

`docker compose up` → `nginx` (LB) → `api` ×3 réplicas → `redis`.
Perfil opcional `observability` → Prometheus scrapeando cada réplica.

## Ramas / PRs

| PR | Rama | Contenido | Specs |
|----|------|-----------|-------|
| #1 | `Cristian/InferenceAPI` | Scaffold, specs SDD, tooling, config, logging, health/live | 000, 005 (parcial) |
| #2 | `feat/r1-infer` | Auth, rate limit, cache, single-flight, breaker, `/v1/infer` | 001, 004 |
| #3 | `feat/r2-stream` | SSE, backpressure, cancelación, auditoría | 002 |
| #4 | `feat/r3-classify` | Modelos v1/v2, registry, batching | 003 |
| #5 | `feat/ops` | Docker, nginx, métricas, health completo, locust | 004, 005 |
| #6 | `docs/architecture` | DECISIONS.md final, C4, README | — |
