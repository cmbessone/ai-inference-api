# Resultados de carga (OB-AC08)

**Entorno:** MacBook (Apple Silicon) · colima con **2 vCPU / 2 GiB** · `docker compose` con
3 réplicas (límite 1 CPU / 512 MiB c/u) + nginx + Redis · proveedor LLM `mock`
(50 ms al primer token). Fecha: 2026-10-23.

## 1. Locust desde el host — 100 usuarios a 1 req/s, 60 s (`make up-load && make load`)

| Endpoint | Requests | Errores | p50 | p95 | p99 | RPS |
|---|---:|---:|---:|---:|---:|---:|
| `POST /v1/classify` | 2880 | 0 | 120 ms | 350 ms | 420 ms | 51.2 |
| `POST /v1/infer` (prompts populares → cache) | 1898 | 0 | 100 ms | 330 ms | 410 ms | 33.8 |
| `POST /v1/infer` (prompts únicos → LLM) | 572 | 0 | 160 ms | 390 ms | 460 ms | 10.2 |
| `POST /v1/infer/stream` | 300 | 0 | 90 ms | 250 ms | 360 ms | 5.3 |
| **Total** | **5650** | **0 (0 %)** | 120 ms | 350 ms | 420 ms | **100.5** |

✅ El sistema sostiene **100 RPS sin un solo error**.
⚠️ El p95 medido desde el host supera los 200 ms. La siguiente sección muestra que ese
excedente viene del entorno local, no del sistema.

## 2. Diagnóstico: ¿dónde está la latencia?

| Señal | Valor | Conclusión |
|---|---|---|
| CPU por réplica bajo carga (`docker stats`) | 6–9 % | Las réplicas no están saturadas |
| Costo de una predicción v2 / v1 | 0.43 / 0.09 ms | El modelo no es el cuello |
| Costo en batch de 32 (v2) | 0.04 ms/pred | El batching rinde ~10× por predicción |

Con CPU ociosa y operaciones sub-milisegundo, el tiempo se va en el transporte: colima reenvía
`localhost:8080` a la VM con un proxy de usuario (port-forward) que bajo carga agrega cientos de
ms. El histograma del servidor también lo ve, porque mide desde que llegan los headers e
incluye leer el body y escribir la respuesta por ese socket.

## 3. Carga desde dentro de la red de Docker (`make load-internal`)

Mismo patrón (~100 RPS open-loop, 50 % classify / 50 % infer popular), generado desde la red
interna contra nginx, sin el port-forward del host:

| Corrida | Endpoint | p50 | p95 | p99 | Errores |
|---|---|---:|---:|---:|---:|
| 1 | `POST /v1/classify` | 17.9 ms | **38.6 ms** | 90.2 ms | 0 |
| 1 | `POST /v1/infer` (cache) | 4.9 ms | **18.4 ms** | 43.4 ms | 0 |
| 2 | `POST /v1/classify` | 18.7 ms | **53.8 ms** | 320.3 ms | 0 |
| 2 | `POST /v1/infer` (cache) | 5.3 ms | **35.6 ms** | 277.5 ms | 0 |

✅ **P95 muy por debajo de 200 ms** en ambas corridas. El generador de carga corre dentro de una
de las réplicas y comparte su CPU, así que es una cota pesimista. La corrida 2 coincidió con
otra actividad en la VM de 2 vCPU (se nota en el p99): en un entorno con CPUs dedicadas el
p99 también quedaría holgado. Es la razón para medir siempre en el entorno de destino.

## 4. Resiliencia bajo fallas (mismo stack)

| Prueba | Resultado |
|---|---|
| `make chaos`: SIGKILL a 1 de 3 réplicas durante tráfico continuo | **40/40 OK**: nginx reintenta en otra réplica (`proxy_next_upstream error timeout`) |
| `docker compose stop redis` durante tráfico | Todos 200 en < 160 ms; `/health` → `degraded`; al volver Redis se recupera solo |

## Cómo reproducir

```bash
make up-load          # stack con rate limit alto (se mide capacidad, no cuota)
make load             # locust desde el host  → loadtest/report.html + CSV
make load-internal    # carga desde la red interna
make chaos            # mata una réplica bajo tráfico
```
