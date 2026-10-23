# Tareas

Cada tarea referencia los criterios de aceptación que cubre. `[x]` = implementada y con test.

## PR #1 — Scaffold
- [x] T01 Estructura de carpetas, `pyproject.toml`, `Makefile`, ruff + mypy strict
- [x] T02 `Settings` inmutables con defaults seguros (P8)
- [x] T03 Logging JSON + `request_id` en contextvars (OB-AC01, OB-AC02)
- [x] T04 `create_app()` + `lifespan` + `/health/live` (OB-AC05)

## PR #2 — R1 inferencia sincrónica
- [ ] T05 Puertos (`core/ports.py`) y errores de dominio
- [ ] T06 `RedisGateway` con timeout + breaker local (RS-AC11)
- [ ] T07 Autenticación por API key hasheada (R1-AC01..03)
- [ ] T08 Ventana deslizante pura + Lua + fallback local (R1-AC04..08)
- [ ] T09 `cache_key()` pura + `RedisCache` (R1-AC09..12)
- [ ] T10 Circuit breaker: máquina de estados pura + stores Redis/memoria (RS-AC01..08)
- [ ] T11 `ResilientLLMClient`: breaker, timeout, bulkhead, retry con jitter (R1-AC15, R1-AC16, RS-AC09, RS-AC12)
- [ ] T12 Proveedores `mock` y `openai` + factory
- [ ] T13 Single-flight local + distribuido (R1-AC17)
- [ ] T14 `InferenceService` + ruta `/v1/infer` (R1-AC13, R1-AC14)

## PR #3 — R2 streaming
- [ ] T15 Formato SSE puro (R2-AC01)
- [ ] T16 Relay productor/consumidor con buffer acotado (R2-AC07, R2-AC08)
- [ ] T17 Cancelación upstream por desconexión y timeout (R2-AC03, R2-AC04)
- [ ] T18 `AuditSink` con cola acotada + worker (R2-AC05)
- [ ] T19 Ruta `/v1/infer/stream` (R2-AC02, R2-AC06)

## PR #4 — R3 clasificación
- [ ] T20 Script de entrenamiento v1/v2 + manifest con SHA-256 (R3-AC08)
- [ ] T21 `ModelRegistry` cargado en lifespan fuera del loop (R3-AC01, R3-AC02)
- [ ] T22 Resolución de versión pura + 400 (R3-AC03..05)
- [ ] T23 Inferencia en thread pool acotado (R3-AC06, R3-AC07)
- [ ] T24 `MicroBatcher` (R3-AC09)

## PR #5 — Operación
- [ ] T25 Load shedding ASGI (RS-AC10)
- [ ] T26 Métricas Prometheus (OB-AC07)
- [ ] T27 `/health` y `/health/ready` (OB-AC04, OB-AC06)
- [ ] T28 Dockerfile multi-stage, compose con 3 réplicas + nginx + redis (RS-AC14)
- [ ] T29 Graceful shutdown (RS-AC13)
- [ ] T30 Locust + resultados (OB-AC08)

## PR #6 — Documentación
- [ ] T31 `DECISIONS.md` con ADRs y escenarios A/B/C
- [ ] T32 Diagramas C4 (contexto, contenedores, componentes) + dinámicos
- [ ] T33 README con ejemplos de los 3 endpoints + colección Bruno
