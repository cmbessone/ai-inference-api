# syntax=docker/dockerfile:1.7

# ── Etapa 1: dependencias + entrenamiento de modelos ────────────────────────────
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1

# Capa cacheable: sólo se invalida si cambian las dependencias. Sin `RUN --mount=type=cache`
# a propósito: algunos builders (Railway) exigen un id propio de la plataforma y el
# Dockerfile debe ser portable; el cache de capas ya evita reinstalar.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Los artefactos del modelo se generan en el build: imagen inmutable y reproducible (R3-AC08).
COPY ml ./ml
RUN .venv/bin/python -m ml.train --out /app/models

# ── Etapa 2: runtime mínimo, sin toolchain, usuario no-root ─────────────────────
FROM python:3.12-slim AS runtime
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/models /app/models
COPY app ./app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ML_MODELS_DIR=/app/models \
    PORT=8000

USER app
EXPOSE 8000

# PORT: las plataformas (Railway, Cloud Run, Heroku) inyectan el puerto; en local queda 8000.
HEALTHCHECK --interval=5s --timeout=2s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health/ready', timeout=2)"

# Un proceso por contenedor: se escala con réplicas (orquestador), no con workers internos.
# --timeout-graceful-shutdown: ante SIGTERM termina requests/streams en curso (RS-AC13).
# --timeout-keep-alive 75 > keepalive del LB (60) para evitar carreras al reusar conexiones.
# Forma shell + `exec`: expande $PORT y deja a uvicorn como PID 1 (recibe SIGTERM directo).
CMD exec uvicorn app.main:create_app --factory \
    --host 0.0.0.0 --port "${PORT}" \
    --loop uvloop --http httptools \
    --timeout-graceful-shutdown 30 --timeout-keep-alive 75 \
    --no-access-log --proxy-headers --forwarded-allow-ips "*"
