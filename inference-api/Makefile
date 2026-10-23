.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv
COMPOSE ?= docker compose
REPLICAS ?= 3

.PHONY: help install models lint format typecheck test cov up down logs ps load clean

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

up: ## Levanta nginx + N réplicas + redis
	$(COMPOSE) up --build -d --scale api=$(REPLICAS)
	@echo "API en http://localhost:8080  ·  docs en http://localhost:8080/docs"

down: ## Baja el stack
	$(COMPOSE) down -v

logs: ## Sigue los logs de las réplicas
	$(COMPOSE) logs -f api

ps: ## Estado de los contenedores
	$(COMPOSE) ps

load: ## Prueba de carga headless: 100 RPS durante 60 s contra el stack levantado
	$(UV) run --group load locust -f loadtest/locustfile.py --headless \
		-u 120 -r 40 -t 60s --host http://localhost:8080 \
		--csv loadtest/results --html loadtest/report.html

clean: ## Borra artefactos locales
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov models
