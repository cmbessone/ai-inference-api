# 001 — Inferencia sincrónica (R1)

**Estado:** aprobada · **Prioridad:** obligatoria · **Endpoint:** `POST /v1/infer`

## Historia

Como **cliente de la plataforma** quiero enviar un prompt a un modelo de lenguaje y recibir la
respuesta completa en una sola llamada HTTP, con métricas de uso, para integrarlo en mi producto
sin manejar streaming.

## Contrato

```http
POST /v1/infer
X-API-Key: <key>
Content-Type: application/json

{ "model": "gpt-4o", "input": "...", "config": { "temperature": 0.7, "max_tokens": 256 }, "timeout_s": 30 }
```

```jsonc
// 200 OK
{ "request_id": "uuid", "output": "...", "model": "gpt-4o",
  "usage": { "tokens_in": 42, "tokens_out": 128 }, "latency_ms": 185, "cache_hit": false }

// 429 Too Many Requests   (+ header Retry-After)
{ "error": "rate_limit_exceeded", "retry_after_s": 12 }
```

| Campo | Regla |
|-------|-------|
| `model` | Obligatorio. Debe pertenecer a `LLM_ALLOWED_MODELS`. |
| `input` | Obligatorio. 1..`MAX_INPUT_CHARS` caracteres. |
| `config.temperature` | Opcional, `0.0..2.0`, default `0.7`. |
| `config.max_tokens` | Opcional, `1..4096`, default `256`. |
| `timeout_s` | Opcional, `> 0` y `<= LLM_MAX_TIMEOUT_S`, default `LLM_DEFAULT_TIMEOUT_S` (30). |

## Criterios de aceptación

### Autenticación
- **R1-AC01** — Dado un request sin header `X-API-Key`, cuando llega a cualquier ruta `/v1/*`,
  entonces responde `401 {"error": "unauthorized"}`.
- **R1-AC02** — Dado un `X-API-Key` inválido, entonces responde `401` y **no** consume cuota de rate limit.
- **R1-AC03** — Dado un `X-API-Key` válido, entonces el request se asocia a su `client_id`
  (usado en rate limit y logs). Las keys se comparan por hash, nunca en texto plano.

### Rate limiting
- **R1-AC04** — Dado un cliente que hizo 60 requests en los últimos 60 s, cuando hace el request 61,
  entonces responde `429` con `{"error": "rate_limit_exceeded", "retry_after_s": N}` y header `Retry-After: N`,
  donde `N` es el tiempo exacto (redondeado hacia arriba) hasta que se libere un cupo.
- **R1-AC05** — El límite es por `client_id` y **global entre réplicas**: dos réplicas comparten la cuota.
- **R1-AC06** — La ventana es deslizante (no fija): no se permiten ráfagas de 120 requests en el borde de dos minutos.
- **R1-AC07** — Las respuestas exitosas incluyen `X-RateLimit-Limit` y `X-RateLimit-Remaining`.
- **R1-AC08** — Dado que Redis no está disponible, entonces el rate limit sigue funcionando con un
  limitador local por réplica (*fail-open controlado*) y se emite la métrica `redis_fallback_total`.

### Cache
- **R1-AC09** — Dado un request idéntico (mismo `model`, mismo `input` normalizado y misma `config`)
  a uno respondido dentro del TTL, entonces responde desde cache con `cache_hit: true`, sin llamar al LLM.
- **R1-AC10** — Cambiar `model` o `config` produce una entrada de cache distinta.
- **R1-AC11** — El TTL es configurable (`CACHE_TTL_S`).
- **R1-AC12** — Dado que Redis no está disponible, entonces el request se resuelve contra el LLM (bypass de cache), sin error.

### Respuesta
- **R1-AC13** — Toda respuesta 200 incluye `request_id` (UUID), `usage.tokens_in`, `usage.tokens_out`,
  `latency_ms` y `cache_hit`. El `request_id` también va en el header `X-Request-ID`.
- **R1-AC14** — Dado un `model` no permitido o un body inválido, entonces responde `422`/`400` con un error claro.
- **R1-AC15** — Dado que el circuito hacia el LLM está abierto, entonces responde
  `503 {"error": "llm_unavailable", "retry_after_s": N}` sin esperar al proveedor.
- **R1-AC16** — Dado que el LLM excede `timeout_s`, entonces responde `504 {"error": "llm_timeout"}`.

### Thundering herd (Escenario A)
- **R1-AC17** — Dados 100 requests idénticos concurrentes tras un cache miss, entonces se realiza
  **una sola** llamada al LLM (*request coalescing*), dentro de una réplica y entre réplicas.

## Fuera de alcance
- Cache *semántico* por embeddings (similitud). Ver `DECISIONS.md` → ADR-004.
