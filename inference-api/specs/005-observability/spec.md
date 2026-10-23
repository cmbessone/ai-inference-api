# 005 — Observabilidad

**Estado:** aprobada · **Prioridad:** obligatoria (logs, health) / bonus (métricas, carga)

## Logs estructurados

- **OB-AC01** — Cada request genera exactamente una línea de log JSON de acceso con, como mínimo:
  `timestamp` (ISO-8601), `request_id`, `client_id`, `model`, `latency_ms`, `cache_hit`,
  `status ∈ {"success", "error", "timeout"}`.
- **OB-AC02** — El `request_id` se toma del header `X-Request-ID` si viene y es válido; si no, se genera un UUID4.
  Se propaga a todos los logs del request (contextvars) y se devuelve en la respuesta.
- **OB-AC03** — Nunca se loguean API keys ni el contenido de prompts en el log de acceso (el contenido va sólo a auditoría).

## Healthchecks

- **OB-AC04** — `GET /health` responde:
  `{"status": "ok"|"degraded", "redis": "ok"|"down", "llm_circuit": "closed"|"open"|"half-open", "models_loaded": ["v1","v2"]}`.
  `degraded` si Redis está caído o el circuito no está cerrado. HTTP 200 salvo que no haya modelos cargados (503).
- **OB-AC05** — `GET /health/live` (liveness) responde 200 si el proceso atiende requests, sin tocar dependencias.
- **OB-AC06** — `GET /health/ready` (readiness) responde 503 hasta que los modelos estén cargados y durante el shutdown.

## Métricas (bonus)

- **OB-AC07** — `GET /metrics` expone en formato Prometheus, como mínimo: requests totales por ruta/status,
  histograma de latencia, contador de cache hit/miss (de donde sale el *hit ratio*), estado del circuito,
  rechazos por rate limit, cancelaciones de stream y tamaño de batch de clasificación.

## Prueba de carga (bonus)

- **OB-AC08** — `loadtest/locustfile.py` mantiene 100 RPS contra el sistema completo (`docker compose`) con
  error rate < 1 % y P95 < 200 ms en endpoints sincrónicos; los resultados se documentan en `loadtest/RESULTS.md`.
