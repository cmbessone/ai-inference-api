.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv
COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo docker-compose)

.PHONY: help install models lint format typecheck test cov up up-load down logs ps load load-internal chaos trace clean

help: ## Lista los comandos disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Instala dependencias (incluye dev)
	$(UV) sync --all-groups

models: ## Entrena y serializa los modelos v1/v2 en ./models
	$(UV) run python -m ml.train --out models

lint: ## Ruff (lint + formato) y mypy --strict
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy

format: ## Aplica formato y autofixes
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## Sólo mypy
	$(UV) run mypy

test: ## Tests unitarios + de API (sin docker)
	$(UV) run pytest -m "not integration"

cov: ## Tests con reporte de cobertura
	$(UV) run pytest -m "not integration" --cov --cov-report=term-missing

up: ## Levanta nginx + 3 réplicas + redis (espera a que estén sanas)
	$(COMPOSE) up --build -d --wait
	@echo "API en http://localhost:8080  ·  docs en http://localhost:8080/docs"

up-load: ## Igual que `up` pero con rate limit alto para pruebas de carga
	$(COMPOSE) -f docker-compose.yml -f deploy/compose.loadtest.yml up --build -d --wait

down: ## Baja el stack
	$(COMPOSE) down -v

logs: ## Sigue los logs de las réplicas
	$(COMPOSE) logs -f api

ps: ## Estado de los contenedores
	$(COMPOSE) ps

load: ## Prueba de carga headless: ~100 RPS durante 60 s contra el stack (`make up-load`)
	$(UV) run --group load locust -f loadtest/locustfile.py --headless \
		-u 100 -r 50 -t 60s --host http://localhost:8080 \
		--csv loadtest/results --html loadtest/report.html

load-internal: ## Carga desde dentro de la red de Docker (sin el port-forward del host)
	docker cp loadtest/in_network.py $$($(COMPOSE) ps -q api | awk 'NR == 1'):/tmp/in_network.py
	docker exec $$($(COMPOSE) ps -q api | awk 'NR == 1') python /tmp/in_network.py http://nginx

chaos: ## Demo HA: mata una réplica y muestra que el servicio sigue respondiendo
	./scripts/chaos.sh

trace: ## Matriz de trazabilidad SDD (criterio → tests); falla si falta alguno
	$(UV) run python scripts/trace.py

clean: ## Borra artefactos locales
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov models
