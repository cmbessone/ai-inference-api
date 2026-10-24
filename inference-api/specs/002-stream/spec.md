# 002 — Streaming SSE con resiliencia (R2)

**Estado:** aprobada · **Prioridad:** obligatoria · **Endpoint:** `POST /v1/infer/stream`

## Historia

Como **cliente** quiero recibir los tokens a medida que el modelo los genera (Server-Sent Events)
para mostrar la respuesta en tiempo real, y quiero que el sistema no desperdicie recursos si me desconecto.

## Contrato

Mismo body y autenticación que `POST /v1/infer`. Respuesta `200 text/event-stream`:

```
data: {"token": "Hola", "index": 0}

data: {"token": " mundo", "index": 1}

data: [DONE]
```

Errores dentro del stream (el status HTTP ya fue enviado):

```
data: {"error": "llm_unavailable", "retry_after_s": 45}     # circuito abierto
data: {"error": "llm_timeout"}                                # timeout total excedido
data: {"error": "llm_error"}                                  # fallo del proveedor a mitad de stream
data: {"error": "slow_consumer"}                              # el cliente no consume a tiempo
```

## Criterios de aceptación

- **R2-AC01** — Cada token se emite como un evento `data:` con `{"token", "index"}`, `index` empezando en 0
  y consecutivo. Al finalizar correctamente se emite `data: [DONE]`.
- **R2-AC02** — Dado que el circuito está abierto, entonces se emite un único evento
  `{"error": "llm_unavailable", "retry_after_s": N}` y se cierra el stream sin llamar al proveedor.
- **R2-AC03** — Dado que el cliente se desconecta, entonces la llamada upstream al proveedor se **cancela**
  (se cierra la conexión HTTP al LLM) y se registra la métrica `stream_cancelled_total{reason="client_disconnect"}`.
- **R2-AC04** — El timeout total es configurable por request (`timeout_s`, default 30 s). Al excederse se emite
  `{"error": "llm_timeout"}` y se cancela el upstream.
- **R2-AC05** — El stream completo (input, output, tokens, duración, motivo de fin) se registra para auditoría
  **sin bloquear la respuesta**: el registro se encola en un worker en background; si la cola está llena
  se descarta y se cuenta en `audit_dropped_total` (nunca se frena al cliente).
- **R2-AC06** — Las respuestas incluyen `Cache-Control: no-cache` y `X-Accel-Buffering: no`.

### Backpressure / slow consumer (Escenario C)
- **R2-AC07** — Entre el proveedor y el cliente hay un buffer **acotado** (`STREAM_BUFFER_SIZE` tokens).
  Si el buffer se llena, se deja de leer del proveedor (la presión se propaga hasta el socket TCP upstream).
- **R2-AC08** — Si el buffer permanece lleno más de `STREAM_SLOW_CONSUMER_TIMEOUT_S`, entonces se cancela el
  upstream, se emite `{"error": "slow_consumer"}` y se libera la memoria.
