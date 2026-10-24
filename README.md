# AI Inference API

[![CI](https://github.com/cmbessone/ai-inference-api/actions/workflows/ci.yml/badge.svg)](https://github.com/cmbessone/ai-inference-api/actions/workflows/ci.yml)

Solución al challenge técnico **Python Backend (Senior)**: API de inferencia escalable y de alta
disponibilidad con FastAPI, Redis, streaming SSE y un modelo de ML propio embebido.

El proyecto vive en [`inference-api/`](inference-api/), con la estructura que pide el enunciado.

| Para… | Ir a |
|---|---|
| Entender la arquitectura y los trade-offs ⭐ | [`inference-api/DECISIONS.md`](inference-api/DECISIONS.md) |
| Levantarlo y probar los endpoints | [`inference-api/README.md`](inference-api/README.md) |
| Ver los diagramas C4 | [`inference-api/docs/architecture/c4.md`](inference-api/docs/architecture/c4.md) |
| Leer las specs (SDD) y la trazabilidad | [`inference-api/specs/`](inference-api/specs/README.md) |
| Ver los resultados de carga y resiliencia | [`inference-api/loadtest/RESULTS.md`](inference-api/loadtest/RESULTS.md) |

## Quickstart

```bash
cd inference-api
docker compose up --build        # nginx :8080 → 3 réplicas → redis, sin configuración extra
curl localhost:8080/health
```

API key de demo para uso local: `demo-key-123`. Ejemplos de los 3 endpoints en el
[README del proyecto](inference-api/README.md) y en la colección Bruno (`inference-api/bruno/`).

## Demo en producción

https://api-production-5ab0.up.railway.app/docs — Railway, 3 réplicas + Redis, deploy automático
en cada merge a `main`. La API key se comparte por separado.

## Cómo se construyó

- **Spec-Driven Development:** specs con 56 criterios de aceptación → plan → tareas → tests que
  nombran cada criterio (`make trace` → 56/56).
- **Un PR por requisito** (#1–#6), cada uno validado por CI (lint + tipos, tests, `docker
  compose` con smoke test y prueba de caos).
