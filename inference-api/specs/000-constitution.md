# 000 — Constitución del proyecto

> Principios no negociables. Toda spec, plan y PR se evalúa contra este documento.
> Si una decisión los viola, se documenta como excepción explícita en `DECISIONS.md`.

## Metodología: Spec-Driven Development (SDD)

1. **Spec primero.** Ninguna funcionalidad se implementa sin una spec con criterios de
   aceptación numerados (`R1-AC01`, `R2-AC03`, …) en formato *Dado / Cuando / Entonces*.
2. **Plan después.** `specs/plan.md` traduce las specs a componentes y decisiones técnicas.
3. **Tareas trazables.** `specs/tasks.md` divide el plan en tareas; cada tarea referencia criterios.
4. **Tests derivados de la spec.** Cada criterio de aceptación tiene al menos un test que lo
   nombra en su docstring. La matriz de trazabilidad vive en `specs/README.md`.
5. **La spec es la fuente de verdad.** Si el código y la spec divergen, se corrige uno de los dos
   en el mismo PR; nunca se deja la divergencia.

## Principios de arquitectura

| # | Principio | Consecuencia concreta |
|---|-----------|------------------------|
| P1 | **Réplicas stateless** | Ningún estado que deba ser consistente entre réplicas vive en memoria del proceso. Rate limit, cache, locks y estado del circuit breaker viven en Redis. Escalar = agregar réplicas. |
| P2 | **Async de punta a punta** | Nada bloquea el event loop: I/O con `httpx`/`redis.asyncio`; CPU (ML) en un thread pool acotado. Prohibido `requests` y `time.sleep`. |
| P3 | **Degradación antes que caída** | Si una dependencia falla (Redis, LLM), el sistema responde peor pero responde. Cada dependencia tiene un fallback documentado. |
| P4 | **Fallar rápido y acotado** | Todo I/O tiene timeout. Toda cola tiene tamaño máximo. Toda concurrencia tiene límite (bulkhead). |
| P5 | **Núcleo funcional, cáscara imperativa** | La lógica de negocio (decisiones de rate limit, estado del breaker, claves de cache, formato SSE) son funciones puras sobre datos inmutables. Los efectos (Redis, red, reloj) quedan en los bordes y se inyectan. |
| P6 | **Puertos y adaptadores** | El dominio depende de `Protocol`s, no de librerías. Cambiar Redis, el proveedor LLM o el runtime de ML es escribir un adaptador nuevo. |
| P7 | **Observabilidad desde el día 1** | Logs JSON con `request_id`, métricas Prometheus y healthchecks que distinguen *liveness* de *readiness*. |
| P8 | **Configuración externa** | Todo parámetro operativo (límites, TTL, timeouts, umbrales) es configurable por variable de entorno con un default seguro. |

## Estándares de código

- Python 3.12, tipado estricto (`mypy --strict`), `ruff` (lint + format) sin warnings.
- Funciones puras siempre que sea posible; `@dataclass(frozen=True)` para valores.
- Sin estado global mutable: las dependencias se construyen en una *composition root*
  (`app/container.py`) y se inyectan.
- Errores de dominio tipados; el mapeo a HTTP se hace en un único lugar (`app/api/errors.py`).
- Cada patrón de diseño usado se justifica en `DECISIONS.md` (por qué este y no otro).

## Flujo de trabajo

- Ramas por feature, PRs hacia `main`. **Nunca push directo a `main`.**
- `make lint test` en verde antes de abrir un PR.
- Commits con [Conventional Commits](https://www.conventionalcommits.org/).
