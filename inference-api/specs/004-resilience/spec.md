# 004 — Resiliencia y alta disponibilidad (transversal)

**Estado:** aprobada · **Prioridad:** obligatoria (breaker) / diferencial (resto)

## Objetivo

Cumplir el SLA de 99.9 % de disponibilidad: el sistema debe **degradarse** ante fallas de sus
dependencias en lugar de caerse, y debe poder escalar horizontalmente agregando réplicas.

## Circuit breaker hacia el proveedor LLM (Escenario B)

```
           5 fallos en 30 s                      pasaron 60 s
 CLOSED ─────────────────────▶ OPEN ───────────────────────────▶ HALF_OPEN
   ▲                             ▲                                  │
   │        probe exitoso        │          probe falla             │
   └─────────────────────────────┼──────────────────────────────────┘
                                 └──────────────────────────────────┘
```

- **RS-AC01** — Dados 5 fallos (timeout, error de conexión o 5xx del proveedor) dentro de una ventana deslizante
  de 30 s, el circuito pasa a `OPEN` durante 60 s.
- **RS-AC02** — En `OPEN` los requests fallan inmediatamente con `llm_unavailable` y `retry_after_s` = tiempo restante.
- **RS-AC03** — Pasados los 60 s el circuito pasa a `HALF_OPEN` y permite **un único** request de prueba (*probe*)
  en todo el clúster; el resto sigue recibiendo `llm_unavailable`.
- **RS-AC04** — Si el probe tiene éxito, el circuito vuelve a `CLOSED` y se limpia el historial de fallos.
  Si falla, vuelve a `OPEN` por otros 60 s.
- **RS-AC05** — Errores del cliente (4xx del proveedor, validación) **no** cuentan como fallos.
- **RS-AC06** — El estado del breaker es **compartido entre réplicas** (Redis): todas abren y cierran juntas.
- **RS-AC07** — Si Redis no está disponible, cada réplica usa un breaker local con la misma política.
- **RS-AC08** — Umbral, ventana y duración son configurables (`CB_FAILURE_THRESHOLD`, `CB_WINDOW_S`, `CB_OPEN_S`).

## Protección de dependencias

- **RS-AC09** — *Bulkhead*: como máximo `LLM_MAX_CONCURRENCY` llamadas simultáneas al LLM por réplica; el exceso
  espera hasta `LLM_QUEUE_TIMEOUT_S` y luego falla con `503 overloaded`.
- **RS-AC10** — *Load shedding*: si una réplica tiene más de `MAX_INFLIGHT_REQUESTS` requests en curso, responde
  `503 {"error": "overloaded"}` con `Retry-After` antes de hacer trabajo.
- **RS-AC11** — Toda operación contra Redis tiene timeout corto (`REDIS_SOCKET_TIMEOUT_S`). Tras fallos repetidos,
  un breaker local deja de intentar Redis durante unos segundos para no penalizar la latencia.
- **RS-AC12** — Reintentos sólo para errores transitorios, con *exponential backoff + jitter*, acotados por el
  tiempo restante del request y nunca con el circuito abierto, ni en streaming después del primer token.

## Ciclo de vida

- **RS-AC13** — *Graceful shutdown*: ante SIGTERM la réplica deja de aceptar requests, termina los que están en curso
  (incluidos streams) hasta un tiempo máximo y vacía la cola de auditoría.
- **RS-AC14** — `docker compose up` levanta N réplicas detrás de un balanceador; matar una réplica no corta el servicio.
