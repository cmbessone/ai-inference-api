# 003 — Modelo ML propio embebido (R3)

**Estado:** aprobada · **Prioridad:** obligatoria · **Endpoint:** `POST /v1/classify`

## Historia

Como **cliente** quiero clasificar el sentimiento de un texto con el modelo propio de la plataforma,
eligiendo la versión del modelo, para comparar versiones o fijar una en producción.

## Contrato

```http
POST /v1/classify
X-API-Key: <key>
X-Model-Version: v2
Content-Type: application/json

{ "input": "texto a clasificar" }
```

```jsonc
// 200 OK
{ "label": "positive", "confidence": 0.94, "model_version": "v2", "latency_ms": 12 }

// 400 Bad Request
{ "error": "unknown_model_version", "detail": "Versión 'v9' no existe. Disponibles: v1, v2" }
```

## Criterios de aceptación

- **R3-AC01** — Los modelos se cargan **una sola vez** al arrancar el proceso (en el `lifespan`), nunca por request.
- **R3-AC02** — La carga corre fuera del event loop (`asyncio.to_thread`), sin bloquearlo.
- **R3-AC03** — Se sirven al menos dos versiones (`v1`, `v2`); la versión se elige con el header `X-Model-Version`.
- **R3-AC04** — Sin header, se usa `ML_DEFAULT_VERSION` (default `v2`).
- **R3-AC05** — Dada una versión inexistente, entonces responde `400` con un mensaje que lista las versiones disponibles.
- **R3-AC06** — La inferencia corre en un thread pool acotado (`ML_MAX_THREADS`); nunca en el event loop.
- **R3-AC07** — La respuesta incluye `label`, `confidence ∈ [0, 1]`, `model_version` y `latency_ms`.
- **R3-AC08** — Los artefactos del modelo se verifican con su checksum SHA-256 (manifest) antes de cargarse.

### Bonus — batching dinámico
- **R3-AC09** — Con `ML_BATCHING_ENABLED=true`, los requests concurrentes a la misma versión se acumulan en una
  ventana de `ML_BATCH_WINDOW_MS` (10 ms) o hasta `ML_MAX_BATCH_SIZE`, y se infieren en una sola llamada vectorizada.
  Cada request recibe exactamente su resultado.
