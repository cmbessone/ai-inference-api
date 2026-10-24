# DECISIONS

> Documento de arquitectura y trade-offs. Cada decisión dice **qué** se eligió, **por qué** y
> **qué se descartó**. Los criterios (`R1-AC04`, `RS-AC06`, …) remiten a las specs en
> [`specs/`](specs/README.md) y cada uno tiene al menos un test que lo nombra.

## Índice

1. [Resumen](#1-resumen)
2. [Arquitectura](#2-arquitectura)
3. [Escalabilidad y alta disponibilidad](#3-escalabilidad-y-alta-disponibilidad)
4. [Escenarios de carga A, B y C](#4-escenarios-de-carga)
5. [Decisiones (ADRs)](#5-decisiones-adrs)
6. [Patrones de diseño: dónde y por qué](#6-patrones-de-diseño-dónde-y-por-qué)
7. [Hallazgos durante la implementación](#7-hallazgos-durante-la-implementación)
8. [Resultados medidos](#8-resultados-medidos)
9. [Limitaciones y camino a producción](#9-limitaciones-y-camino-a-producción)

---

## 1. Resumen

| Requerimiento | Estado | Dónde |
|---|---|---|
| R1 `POST /v1/infer`: API key, rate limit 60/min en Redis, cache con TTL, `request_id`/`usage`/`latency_ms` | ✅ | `api/routes/infer.py`, `services/inference.py` |
| R2 SSE: circuit breaker 5 fallos/30 s → 60 s, cancelación upstream, timeout por request, auditoría en background | ✅ | `api/routes/stream.py`, `services/streaming.py`, `core/circuit_breaker.py` |
| R3 `POST /v1/classify`: carga única sin bloquear el loop, v1/v2 por `X-Model-Version`, 400 claro | ✅ | `core/ml_model.py`, `services/classification.py` |
| Escenarios de carga | ✅ **A, B y C implementados** (se pedía al menos uno) | [§4](#4-escenarios-de-carga) |
| Observabilidad: logs JSON, `/health` | ✅ + `/health/live`, `/health/ready` | `core/observability/` |
| Bonus: Prometheus `/metrics`, locust 100 RPS, batching dinámico | ✅ los tres | `loadtest/RESULTS.md` |
| Tests | 110 tests (unit + API + e2e con uvicorn real) en ~2 s + 2 de integración contra el stack | `make test` · `pytest -m integration` |
| Trazabilidad SDD | 56/56 criterios de aceptación con test | `make trace` |
| CI | Cada PR: lint + tipos, tests + trazabilidad, `docker compose` + smoke + caos | `.github/workflows/ci.yml` |
| Calidad | `ruff` (22 familias de reglas) + `mypy --strict` sin errores | `make lint` |

**Idea central:** réplicas *stateless* detrás de un balanceador, con todo el estado compartido
(cuotas, cache, locks, estado del breaker) en Redis mediante operaciones atómicas (Lua). Cada
dependencia tiene un *fallback* para que su caída degrade el servicio en vez de tirarlo.

---

## 2. Arquitectura

Diagramas C4 completos (contexto, contenedores, componentes y dinámicos) en
[`docs/architecture/c4.md`](docs/architecture/c4.md).

```
              clientes (SDK / web / backend)
                          │  HTTPS + X-API-Key
                          ▼
                ┌───────────────────┐
                │  nginx (LB)       │  least_conn · failover · SSE sin buffering
                └─────────┬─────────┘
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   ┌────────────┐  ┌────────────┐  ┌────────────┐   réplicas idénticas y stateless
   │ api #1     │  │ api #2     │  │ api #N     │   (FastAPI + uvicorn, 1 proceso c/u)
   │ modelos v1/v2 en memoria (read-only)        │
   └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
         └───────────────┼───────────────┘
                ┌────────┴────────┐
                ▼                 ▼
        ┌──────────────┐   ┌──────────────┐
        │ Redis        │   │ Proveedor LLM│  (mock por defecto · OpenAI opcional)
        │ cuotas·cache │   └──────────────┘
        │ locks·breaker│
        │ audit stream │
        └──────────────┘
```

### Capas (arquitectura hexagonal)

```
api/        adaptadores de entrada  → HTTP, DTOs, mapeo de errores, dependencias
services/   casos de uso            → orquestación (inferencia, streaming, clasificación)
core/       dominio + puertos       → reglas puras, Protocols, adaptadores de salida
container   composition root        → único lugar que conoce las clases concretas
```

La dirección de dependencias es siempre `api → services → core`. `core` no importa FastAPI.

---

## 3. Escalabilidad y alta disponibilidad

**HA** (*high availability*, alta disponibilidad) significa que el servicio siga respondiendo
cuando falla alguna pieza. El SLA de 99.9 % permite ~43 min de caída por mes. La estrategia es
eliminar *puntos únicos de falla* en la ruta del request y convertir cada falla de dependencia
en degradación.

### 3.1 Cómo escala

| Mecanismo | Efecto |
|---|---|
| **Réplicas stateless** (P1) | Escalar = agregar réplicas (`deploy.replicas`, HPA en Kubernetes). No hay *sticky sessions* ni estado a migrar. |
| **Estado compartido atómico en Redis** | Rate limit, cache, locks y breaker son correctos con N réplicas (scripts Lua de un solo round-trip). |
| **Async end-to-end** | Un proceso atiende cientos de requests concurrentes esperando I/O (LLM, Redis) sin threads. |
| **CPU fuera del loop** | El ML corre en un thread pool acotado; el event loop nunca se bloquea. |
| **Micro-batching** | 50 requests concurrentes de clasificación → 1–3 llamadas vectorizadas (~10× menos CPU por predicción). |
| **Cache + single-flight** | El trabajo caro (LLM) se hace una vez por prompt en todo el clúster. |
| **Un proceso por contenedor** | El orquestador ve y escala cada unidad; métricas y health por proceso, sin modo multiproceso. |
| **least_conn en el LB** | Los streams SSE son largos: balancear por conexiones activas evita réplicas sobrecargadas. |

### 3.2 Cómo sobrevive a fallas

| Falla | Comportamiento | Verificado con |
|---|---|---|
| Muere una réplica | nginx reintenta en otra (`proxy_next_upstream error timeout`) y la marca caída 10 s | `make chaos`: SIGKILL bajo tráfico → **40/40 OK** |
| Redis caído | Rate limit local por réplica, cache en bypass, lock local, breaker local, auditoría al log. Respuestas 200 en < 160 ms, `/health` = `degraded` | `test_degrades_gracefully_without_redis` + prueba en vivo |
| Redis lento | Timeout de socket 250 ms + breaker local (3 fallos → 5 s sin intentar): Redis lento no se vuelve latencia | `test_cooldown_after_repeated_failures` |
| LLM caído / lento | Circuit breaker compartido: tras 5 fallos todas las réplicas responden `llm_unavailable` en microsegundos; un solo *probe* en todo el clúster para detectar la recuperación | `test_redis_store_single_probe_across_cluster` |
| Pico de tráfico | Load shedding por réplica (503 + `Retry-After` antes de trabajar) y bulkhead hacia el LLM; health y métricas exentos para que el orquestador no mate réplicas sanas | `test_load_shedding_rejects_excess` |
| Cliente lento en SSE | Buffer acotado + timeout de slow consumer: la memoria por stream está acotada | `test_backpressure_bounds_buffer_and_aborts_slow_consumer` |
| Deploy / scale-in | `SIGTERM` → readiness 503 → uvicorn termina requests en curso (30 s) → se vacía la cola de auditoría | `test_readiness_fails_during_shutdown`, `test_audit_worker_drains_on_shutdown` |
| Artefacto de modelo corrupto | Checksum SHA-256 contra el manifest: la réplica no arranca (no sirve predicciones basura) | `test_tampered_artifact_is_rejected` |

**Liveness ≠ readiness.** `/health/live` no consulta dependencias a propósito: si Redis cae y
liveness fallara, Kubernetes reiniciaría *todas* las réplicas y una degradación se volvería una
caída total.

---

## 4. Escenarios de carga

### Escenario A — Thundering herd ✅ implementado

> 100 clientes piden lo mismo justo después de un cache miss.

Dos capas de *request coalescing*:

1. **Single-flight local** (`core/single_flight.py`): por réplica, una sola tarea por clave. Los
   demás `await`ean la misma tarea con `asyncio.shield`, así que si el primer cliente se
   desconecta la ejecución sigue y los demás reciben el resultado.
2. **Lock distribuido** (`SET key token NX PX`): entre réplicas, un solo líder por clave. Los
   seguidores sondean el cache con backoff (20 ms → 250 ms).
   - Si el líder **termina**, leen el valor (`Source.COALESCED`).
   - Si el líder **falla**, deja un marcador de error de 2 s y los seguidores fallan igual,
     **sin repetir la llamada** que acaba de fallar.
   - Si el líder **desaparece** (crash), el lock expira por TTL y los seguidores que superan su
     espera llaman ellos mismos: se degrada a "algunas llamadas de más", nunca a un bloqueo.
   - El lock se libera con *compare-and-delete* en Lua: sólo el dueño puede liberarlo.

Tests: `test_100_identical_requests_one_llm_call_in_one_replica` (100 → **1** llamada) y
`test_identical_requests_across_replicas_one_llm_call` (3 réplicas × 34 → **1** llamada).

### Escenario B — Proveedor LLM caído ✅ implementado

> El proveedor empieza a dar timeouts.

1. **Timeout por request** (default 30 s, máx. 120 s): ningún request espera para siempre.
2. **Circuit breaker distribuido**: 5 fallos en 30 s → `OPEN` 60 s para todo el clúster.
   Durante ese tiempo cada request falla en microsegundos con `503 llm_unavailable` +
   `retry_after_s` exacto, y SSE emite `{"error": "llm_unavailable", "retry_after_s": N}`.
3. **Half-open con un solo probe**: pasado el minuto, una única réplica prueba (`SET NX` en Lua);
   si sale bien cierra para todos, si falla reabre.
4. **Retries acotados**: sólo errores transitorios, *full jitter*, dentro del presupuesto de
   tiempo y nunca con el circuito abierto: los reintentos no amplifican la caída.
5. **Bulkhead**: aun antes de que abra el circuito, el semáforo limita cuántas llamadas lentas
   pueden acumularse, así que el resto de la API (clasificación, health) sigue sana.
6. **El sistema no se cae completo**: `/v1/classify`, el cache y health siguen funcionando;
   los prompts cacheados se siguen sirviendo aunque el LLM esté caído.

### Escenario C — Slow consumer en streaming ✅ implementado

> Un cliente lee el SSE muy lento. ¿Qué pasa con el buffer? ¿Se cancela upstream?

```
proveedor ──▶ productora ──▶ Queue(maxsize=64) ──▶ generador ──▶ uvicorn ──▶ socket
                  ▲                  │ llena                         │ write buffer lleno
                  └── put() bloquea ─┘ ◀────── deja de drenar ◀──────┘
```

- **Buffer acotado:** como mucho `STREAM_BUFFER_SIZE` tokens en memoria por stream.
- **Backpressure real:** uvicorn implementa control de flujo (`pause_writing`). Si el socket del
  cliente no drena, el generador no avanza, la cola se llena, `put()` bloquea a la productora y
  ésta deja de leer al proveedor: la presión llega hasta el TCP upstream.
- **Slow consumer:** si la cola sigue llena más de `STREAM_SLOW_CONSUMER_TIMEOUT_S` (10 s), se
  cancela el upstream, se descarta el buffer y se emite `{"error": "slow_consumer"}`.
- **Desconexión:** Starlette cancela el generador; su `finally` cancela la productora, que
  cierra el iterador del proveedor (`aclose`) y con eso la conexión HTTP al LLM.
  Verificado de punta a punta con un uvicorn real: `test_client_disconnect_cancels_upstream_end_to_end`.
- **Timeout vs slow consumer:** sólo cuenta como fallo del breaker un timeout mientras se
  esperaba **al proveedor**. Si el lento es el cliente, el proveedor no tiene la culpa y no se
  abre el circuito para todos.

---

## 5. Decisiones (ADRs)

### ADR-001 — Spec-Driven Development
- **Decisión:** specs con criterios numerados (Dado/Cuando/Entonces), luego plan, tareas y
  tests que nombran cada criterio. Matriz de trazabilidad generada con `make trace`.
- **Por qué:** la intención se revisa antes que el código y el evaluador ve cómo cada requisito
  se vuelve un test.
- **Trade-off:** más documentación inicial; se compensa con menos retrabajo.

### ADR-002 — Réplicas stateless y estado en Redis
- **Decisión:** ningún estado que deba ser consistente entre réplicas vive en memoria.
- **Alternativas:** estado en memoria con sticky sessions (no escala ni sobrevive a la caída de
  un nodo); base relacional (latencia y costo innecesarios para datos efímeros con TTL).
- **Trade-off:** Redis es dependencia de consistencia. Se mitiga con fallbacks (ADR-008): si
  cae, se pierde exactitud global (cuota por réplica, sin cache), no disponibilidad.

### ADR-003 — Arquitectura hexagonal + composition root
- **Decisión:** `core/ports.py` define `Protocol`s (`LLMProvider`, `Cache`, `RateLimiter`,
  `BreakerStore`, `DistributedLock`, `AuditSink`, `KeyStore`, `TextClassifier`). Las clases
  concretas sólo se instancian en `container.py`.
- **Por qué:** cambiar Redis por Memcached, OpenAI por Anthropic o sklearn por ONNX es escribir
  un adaptador. Los tests usan dobles sin `mock.patch`.
- **Alternativa descartada:** un contenedor de DI (`dependency-injector`): para este tamaño, un
  factory explícito es más legible.

### ADR-004 — Cache exacto (no semántico por embeddings)
- **Decisión:** clave = `sha256(model + NFC(strip(input)) + config canónica)`. La `config`
  (temperature, max_tokens) entra en la clave porque cambia la salida.
- **Por qué:** el enunciado define "semántico" como "mismo input + mismo modelo". Un cache por
  similitud de embeddings requiere un modelo de embeddings, un índice vectorial y un umbral
  de similitud con riesgo de servir respuestas incorrectas. Queda como evolución (§9).
- **Normalización conservadora:** NFC + trim. No se colapsan espacios internos ni se pasa a
  minúsculas porque cambian el prompt (código, poesía, nombres propios).
- **Producto:** con `temperature > 0` el LLM no es determinista; cachear igual es una decisión
  de producto (consistencia y costo por sobre variedad). Es configurable por TTL.

### ADR-005 — Rate limit con *sliding log* atómico en Lua
- **Alternativas evaluadas:**
  - *Fixed window*: permite 2× en el borde de la ventana (120 requests en 2 s). ❌ R1-AC06.
  - *Token bucket / GCRA* con ráfaga 60: permite > 60 en 60 s consecutivos.
  - *Sliding window counter*: aproximado.
  - ✅ *Sliding log* (ZSET de timestamps): exacto, `retry_after_s` exacto (cuándo vence el más
    viejo) y su costo (≤ 60 miembros por cliente) es trivial.
- **Atomicidad:** un script Lua hace prune + count + add en un round-trip, sin race conditions
  entre réplicas. Los rechazados no consumen cuota.
- **Reloj:** el timestamp lo pasa la app (inyectable y testeable); se asume NTP. Un *skew* de
  ms es irrelevante frente a ventanas de 60 s.
- **Fallback:** limitador local por réplica con la misma función pura (`evaluate_window`).
  Durante la caída de Redis el límite efectivo es N × 60. Es preferible a rechazar todo
  (fail-closed) o no limitar nada (fail-open puro).

### ADR-006 — Auth y rate limit como dependencias de router, no middleware ASGI
- **Por qué:** se aplican sólo a `/v1/*` (health y metrics abiertos para el orquestador),
  aparecen en OpenAPI como esquema de seguridad, se reemplazan en tests y respetan el orden
  (auth → rate limit, así una key inválida no consume cuota de nadie).
- Se respeta la estructura pedida (`api/middleware/auth.py`, `rate_limit.py`).
- **Por qué no `BaseHTTPMiddleware`:** envuelve el body y rompe el streaming y la cancelación.
  Los middlewares que sí son transversales (contexto, load shedding) son ASGI puros.
- **Keys hasheadas:** se guarda `sha256(key) → client_id`. El lookup es por hash (no compara
  secretos carácter a carácter) y un volcado de config no expone las keys.

### ADR-007 — Circuit breaker distribuido con núcleo funcional
- **Decisión:** la máquina de estados son funciones puras (`admit`, `on_failure`,
  `on_success`, `state_of`) sobre un `BreakerSnapshot` inmutable. Los stores las aplican:
  - `RedisBreakerStore`: scripts Lua que replican las mismas reglas, con estado compartido.
  - `InMemoryBreakerStore`: aplica las funciones puras localmente.
  - `FallbackBreakerStore`: Redis y, si cae, memoria (que se mantiene siempre al día).
- **Por qué distribuido:** con 3+ réplicas, un breaker local necesita 5 × N fallos para que todo
  el clúster deje de golpear al proveedor caído, y cada réplica reporta un `retry_after_s`
  distinto. Compartido: todas abren y cierran juntas y hay un único probe.
- **Equivalencia:** `test_stores_follow_same_rules` corre el mismo ciclo contra ambas
  implementaciones.
- **Hash tags** (`{cb:llm}:*`): las claves caen en el mismo slot de Redis Cluster, requisito
  para scripts multi-key.

### ADR-008 — `RedisGateway`: Redis con *fail fast*
- **Problema:** con Redis caído, cada request esperaría el timeout de cada operación
  (rate limit + cache + lock ≈ 3 timeouts). La caída de Redis se volvería latencia.
- **Decisión:** toda operación pasa por un gateway con timeout de 250 ms y un breaker local
  (reutiliza `InMemoryBreakerStore`): tras 3 fallos deja de intentar 5 s y falla en µs con
  `BackendUnavailableError`, que cada adaptador traduce en su fallback.
- **Hallazgo:** redis-py 8 reintenta 3 veces con backoff por defecto. Medido: con Redis caído
  cada operación tardaba ~3 s y la suite pasó de 2 s a 37 s. Se desactivan los retries del
  cliente (`Retry(NoBackoff(), 0)`): la resiliencia vive en un solo lugar.

### ADR-009 — Streaming *push-based* con cola acotada
- **Decisión:** `ResilientLLMClient.stream_into(request, timeout, emit)` empuja tokens a un
  callback en vez de devolver un async generator.
- **Por qué:** `asyncio.timeout` dentro de un generator que hace `yield` es frágil (el timeout
  puede dispararse mientras el generator está suspendido en manos del consumidor). Con *push*,
  el timeout envuelve una corrutina normal y se sabe exactamente si se esperaba al proveedor o
  al cliente (ADR para el breaker, §4-C).
- Productor/consumidor desacoplados por `asyncio.Queue(maxsize)`: backpressure y memoria acotada.

### ADR-010 — Auditoría con cola acotada + Redis Streams
- **Decisión:** `submit()` es síncrono y O(1) (`put_nowait`); un worker del `lifespan` escribe
  en `XADD audit:streams MAXLEN ~ 100000`. Cola llena → se descarta y se cuenta
  (`audit_dropped_total`). Redis caído → se escribe al log estructurado.
- **Por qué no `BackgroundTasks` de Starlette:** corre después de la respuesta en la misma
  tarea; si el cliente se desconecta a mitad del stream no se garantiza que corra, y no hay
  límite de memoria. **Por qué no escribir inline:** bloquearía el stream.
- **Por qué Redis Streams:** ya está en el stack, tiene *consumer groups* para que un servicio
  aparte lo lleve a un data lake, y `MAXLEN` acota la memoria.
- **Trade-off:** ante saturación extrema se pierden registros de auditoría (medido y alertable),
  nunca latencia de clientes. Si la auditoría fuera regulatoria, iría a Kafka con acks.

### ADR-011 — Modelo ML: build-time, checksum, thread pool y batching
- **Entrenamiento en el build de Docker** (`ml/train.py`): cada imagen trae artefactos
  inmutables y reproducibles. v1 = BoW + Naive Bayes, v2 = TF-IDF palabras + chars + LogReg.
- **Integridad:** `manifest.json` con SHA-256 por artefacto, verificado antes de deserializar
  (joblib usa pickle: nunca cargar artefactos no verificados).
- **Carga única** en el `lifespan` con `asyncio.to_thread` (el test mide que el loop siga
  vivo durante una carga lenta). Si falla, la réplica no arranca (fail fast).
- **Warm-up:** una predicción por versión antes de declararse *ready*. Medido: la primera
  predicción en frío tardaba ~600 ms (imports perezosos + thread pool).
- **Thread pool propio** (`anyio.CapacityLimiter`): sklearn/numpy liberan el GIL en buena parte
  del cómputo. No compite con el pool por defecto ni crece sin límite.
- **Micro-batching:** ventana de 10 ms o 64 items. Una predicción v2 cuesta 0.43 ms sola y
  0.04 ms en batch de 32. El worker despacha y sigue acumulando (*pipelining*).
- **Alternativas:** `ProcessPoolExecutor` (evita el GIL pero duplica memoria y serializa
  inputs); un servidor de modelos aparte (Triton, BentoML), que tiene sentido con GPU o modelos
  grandes.

### ADR-012 — Un proceso por contenedor
- **Decisión:** uvicorn sin `--workers`; se escala con réplicas.
- **Por qué:** el orquestador ve cada proceso (health, restart, límites de recursos), las
  métricas no necesitan el modo multiproceso de prometheus_client y el estado local (breaker
  de Redis, single-flight) es coherente por unidad.

### ADR-013 — Load shedding + bulkhead
- **Load shedding** (ASGI, por réplica): con más de `MAX_INFLIGHT_REQUESTS` en curso, responde
  503 + `Retry-After: 1` sin trabajar. Ante un pico conviene rechazar rápido a algunos antes que
  responder tarde a todos. nginx reintenta el 503 en otra réplica.
- **Bulkhead** hacia el LLM (`asyncio.Semaphore`): un proveedor lento no puede acaparar
  conexiones ni memoria; el exceso falla con `overloaded` tras `LLM_QUEUE_TIMEOUT_S`.

### ADR-014 — nginx como balanceador
- `least_conn` por los streams largos; `keepalive` hacia upstream; `proxy_buffering off` sólo
  en la ruta SSE; propaga o genera `X-Request-ID`.
- **Failover:** `proxy_next_upstream error timeout http_503 non_idempotent` con
  `proxy_connect_timeout 500ms`. Ver hallazgo en §7.
- **Producción:** un balanceador gestionado (ALB / Ingress) cumple el mismo rol.

### ADR-015 — Proveedor mock por defecto
- `docker compose up` funciona sin API keys y los tests son deterministas. El mock simula
  latencia y fallas (`MOCK_FAILURE_RATE`) para demostrar el breaker en vivo.
  `LLM_PROVIDER=openai` activa el adaptador real (httpx con pool keep-alive y cancelación al
  cerrar el stream).

### ADR-017 — Deploy en Railway (y no en Vercel)
- **Decisión:** Railway, con la misma imagen Docker: 3 réplicas, healthcheck `/health/ready`,
  solapamiento + drenaje para deploys sin downtime y apagado ordenado, y deploy automático en
  cada merge a `main`.
- **Configuración:** Railway deprecó `railway.json` (deja de leerse el 2026-12-01 y los servicios
  nuevos no pueden usarlo) en favor de `.railway/railway.ts`, que requiere una CLI más nueva.
  Mientras tanto la configuración se aplica por API y queda documentada en el README; migrar a
  ese formato es el siguiente paso.
- **Por qué no serverless (Vercel):** el diseño supone procesos de larga vida. Modelos cargados
  una vez con warm-up, workers en background (auditoría, micro-batcher), single-flight y
  batching por réplica, y streams SSE largos. En funciones efímeras todo eso se pierde o se
  repite en cada arranque en frío.
- **Dockerfile portable:** sin `RUN --mount=type=cache`, porque el builder de Railway exige un
  id de cache propio de la plataforma.
- **Ajustes para una URL pública:** el puerto sale de `$PORT`; con `ENVIRONMENT=production` la
  réplica se niega a arrancar con las API keys de demo (son públicas en el repo); `/metrics`
  admite un `METRICS_TOKEN` opcional.

### ADR-016 — CI por PR con GitHub Actions
- **Decisión:** tres jobs en cada PR y en cada push a `main`:
  `quality` (ruff + mypy --strict), `tests` (pytest con cobertura + `make trace`) y `stack`
  (`docker compose up --wait` con 3 réplicas, smoke test de los endpoints y `scripts/chaos.sh`).
- **PRs apilados:** el workflow vive en la base del stack y corre en `pull_request` sin filtro
  de rama, así valida también los PRs cuya base no es `main`. Los pasos que dependen de
  archivos de PRs posteriores (`Dockerfile`, `scripts/trace.py`) se saltean si todavía no
  existen: cada PR se valida con lo que trae.
- **Fuera del CI, a propósito:** la carga de 100 RPS. En runners compartidos la latencia
  depende de los vecinos, y un umbral de P95 ahí generaría falsos negativos. Queda manual
  (`make load-internal`) y con resultados documentados.
- **Evolución:** escaneo de dependencias e imagen (`pip-audit`, Trivy), publicar la imagen en
  un registry por commit y un job de carga nocturno en un runner dedicado.

---

## 6. Patrones de diseño: dónde y por qué

| Patrón | Dónde | Por qué acá | Alternativa descartada |
|---|---|---|---|
| **Ports & Adapters (Hexagonal)** | `core/ports.py` + adaptadores | Aislar el dominio de Redis/OpenAI/sklearn; testear sin infraestructura | Llamar a las librerías directamente desde las rutas |
| **Composition Root / DI** | `container.py`, `Depends` | Un solo lugar arma el grafo; nada de singletons globales | Contenedor de DI de terceros (más magia, mismo resultado) |
| **Factory** | `create_app()`, `build_provider()` | Crear la app o el proveedor según la config, sin `if` dispersos | Módulo con `app = FastAPI()` global (difícil de testear) |
| **Strategy** | `LLMProvider` (mock/OpenAI), `RateLimiter`, `BreakerStore` | Comportamiento intercambiable detrás de la misma interfaz | Herencia con flags |
| **Decorator** | `ResilientLLMClient` envuelve un `LLMProvider` | Agregar breaker, bulkhead, timeout y retry sin tocar el proveedor | Resiliencia mezclada en cada adaptador |
| **Composite / Fallback** | `FallbackRateLimiter`, `FallbackBreakerStore` | Combinar primario + respaldo con la misma interfaz | `try/except` repetido en cada llamador |
| **State machine (funcional)** | `circuit_breaker.py` | Transiciones como funciones puras `snapshot → snapshot`: testeables y replicables en Lua | Patrón State OO con clases por estado (más código, estado mutable) |
| **Circuit Breaker** | LLM y Redis | Fallar rápido ante una dependencia caída y detectar la recuperación | Sólo timeouts (cada request paga el timeout) |
| **Bulkhead** | Semáforo LLM, `CapacityLimiter` ML | Aislar recursos: una dependencia lenta no hunde al resto | Pool compartido sin límite |
| **Cache-Aside** | `InferenceService` | La app controla qué y cuándo se cachea; cache caído = miss | Read-through (acopla el cache al proveedor) |
| **Single Flight / Request Coalescing** | `SingleFlight` + `RedisLock` | Escenario A: N requests idénticos → 1 llamada | Lock global sin TTL (riesgo de deadlock) |
| **Producer–Consumer (cola acotada)** | SSE relay, auditoría, micro-batcher | Desacoplar velocidades, backpressure, memoria acotada | Colas sin límite (OOM bajo carga) |
| **Registry** | `ModelRegistry` | Versiones como datos inmutables; agregar v3 no toca el código de las rutas | `if version == "v1"` en la ruta |
| **Gateway** | `RedisGateway` | Un único punto con timeout + fail fast para toda la infraestructura | Manejo de errores de Redis en cada adaptador |
| **Functional Core, Imperative Shell** | `evaluate_window`, `cache_key`, `admit`, `format_sse`, `resolve_version`, `backoff_delay`, … | La lógica es pura (sin I/O ni reloj), lo que la vuelve determinista y trivial de testear | Lógica mezclada con I/O |

---

## 7. Hallazgos durante la implementación

Problemas reales encontrados al medir, con su corrección:

| # | Hallazgo | Impacto | Corrección |
|---|---|---|---|
| 1 | redis-py 8 reintenta 3 veces con backoff por defecto | Con Redis caído, ~3 s por operación | `Retry(NoBackoff(), 0)`; la resiliencia queda en `RedisGateway` (ADR-008) |
| 2 | Al devolver una `Response` propia, FastAPI no copia los headers de las dependencias | El stream perdía `X-RateLimit-*` | Se copian explícitamente (`forwarded_headers`) |
| 3 | En FastAPI 0.16x `route.path` es relativo al router | Métricas con `/infer` en vez de `/v1/infer` | `metric_route_label`: path real para rutas estáticas, plantilla con parámetros, `unmatched` en 404 (cardinalidad acotada) |
| 4 | nginx con una réplica muerta: el connect a la IP caída hace timeout y **no** reintentaba | 3 de 40 requests con 504 en la prueba de caos | `proxy_next_upstream ... timeout` + `proxy_connect_timeout 500ms` → **40/40** |
| 5 | Primera predicción en frío ~600 ms | Pico de latencia tras cada deploy | Warm-up antes de *ready* |
| 6 | Import circular `container → api.auth → api.dependencies → container` | — | `KeyStore` movido a `core/auth.py` (el dominio no depende de la API) |
| 7 | El servicio de streaming importaba el contexto desde `api/` | Violación de capas | `RequestContext` movido a `core/observability/context.py` |
| 8 | CI: `curl … \| grep -q` con `pipefail` | El smoke test fallaba de forma intermitente (exit 23): `grep` cierra el pipe al primer match y `curl` muere escribiendo | Guardar la respuesta y buscar después |
| 9 | CI: `docker compose ps -q \| head -n1` con `pipefail` en `chaos.sh` | El caos fallaba a veces (exit 255): `head` cierra el pipe y `docker` recibe SIGPIPE | `awk 'NR == 1'`, que consume toda la salida |

---

## 8. Resultados medidos

Detalle en [`loadtest/RESULTS.md`](loadtest/RESULTS.md).

| Prueba | Resultado |
|---|---|
| Locust 100 usuarios, 60 s, mezcla realista | **100.5 RPS, 0 errores** en 5650 requests |
| Carga desde la red interna (~100 RPS) | P95 classify **38–54 ms**, infer cache **18–36 ms** |
| SIGKILL a 1 de 3 réplicas bajo tráfico | **40/40 OK** |
| Redis detenido bajo tráfico | Todos 200 en < 160 ms; `/health` degraded; recuperación automática |

**Sobre el P95 < 200 ms:** desde el host el P95 fue 350 ms. Con CPU de réplicas en 6–9 % y
operaciones sub-ms, la diferencia es el port-forward host → VM de colima (2 vCPU). Medido desde
dentro de la red, el P95 queda en 18–54 ms. Una inferencia con un LLM real (cache miss) nunca
estará bajo 200 ms: el SLA sync aplica de forma realista a cache hits y a `/classify`.

---

## 9. Limitaciones y camino a producción

| Tema | Hoy | Producción |
|---|---|---|
| Orquestación | docker compose, 3 réplicas fijas | Kubernetes: Deployment + HPA (CPU, in-flight o RPS), PodDisruptionBudget, `preStop` sleep para drenar el endpoint antes del SIGTERM, multi-AZ |
| Redis | Una instancia (SPOF de consistencia, no de disponibilidad) | Redis Sentinel / ElastiCache Multi-AZ. Separar **cache** (LRU, puede perderse) de **estado** (cuotas, breaker) |
| Balanceador | nginx resuelve réplicas al arrancar | ALB / Ingress con descubrimiento dinámico |
| API keys | En variable de entorno | Secret manager o DB + cache local, rotación, scopes y planes por cliente |
| Rate limit | Requests/min | Además, **tokens/min** por cliente (el costo real de un LLM) y cuotas por plan |
| Cache | Coincidencia exacta | Cache semántico con embeddings y umbral de similitud, opt-in por cliente |
| Observabilidad | Logs JSON + Prometheus | OpenTelemetry (trazas distribuidas LB → réplica → Redis → LLM), dashboards y alertas sobre SLO (burn rate) |
| Multi-proveedor | Un proveedor | Fallback entre proveedores (OpenAI → Anthropic) detrás del mismo puerto, con breaker por proveedor |
| ML | sklearn en proceso | ONNX Runtime, model registry (MLflow), canary entre versiones por header o porcentaje |
| Auditoría | Redis Streams con MAXLEN | Kafka + consumidor a data lake si es requisito regulatorio |
| Seguridad | `/metrics` expuesto por nginx | Restringir `/metrics` a la red interna; mTLS entre servicios |
