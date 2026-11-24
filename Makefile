# The Support Desk — local commands.

API_PORT ?= 8000
IMAGE ?= support-desk

.PHONY: help install dev test lint evals docker docker-run smoke ui ui-dev ui-test ui-e2e

help:
	@echo "install   uv sync + mcp-server npm ci/build"
	@echo "dev       uvicorn with reload on :$(API_PORT)"
	@echo "test      pytest"
	@echo "lint      ruff + mypy (guardrails/gate when present)"
	@echo "evals     python -m evals.run_all (P10)"
	@echo "docker    build the image"
	@echo "docker-run  run the image on :$(API_PORT) with a named state volume"
	@echo "smoke     smoke-test a running instance (URL=..., ADMIN_TOKEN=...)"
	@echo "ui        build the UI (served by FastAPI at /)"
	@echo "ui-dev    Vite dev server on :5173 (proxies /api to :$(API_PORT))"
	@echo "ui-test   UI lint, types, unit tests"
	@echo "ui-e2e    Playwright + axe against a throwaway API"

install:
	uv sync
	cd mcp-server && npm ci && npm run build
	cd ui && npm ci

dev:
	uv run uvicorn app.main:app --reload --host 127.0.0.1 --port $(API_PORT)

test:
	uv run pytest -q

lint:
	uv run ruff check app tests
	uv run ruff format --check app tests
	uv run mypy --strict app/guardrails
	@if [ -n "$$(find app/gate -name '*.py' ! -name '__init__.py' 2>/dev/null)" ]; then \
		uv run mypy --strict app/gate; \
	fi

test-cov:
	uv run pytest -q --cov=app/guardrails --cov=app/gate --cov-report=term-missing --cov-fail-under=85

evals:
	uv run python -m evals.run_all

docker:
	docker build -t $(IMAGE) .

# Throwaway container (removed on exit); state persists in the named volume.
docker-run:
	docker run --rm -p $(API_PORT):8000 -e ADMIN_TOKEN -e GROQ_API_KEY \
		-e LLM_PROVIDER=$${LLM_PROVIDER:-groq} -v support-desk-state:/data/state $(IMAGE)

URL ?= http://127.0.0.1:$(API_PORT)
smoke:
	uv run python scripts/smoke.py $(URL)

ui:
	cd ui && npm run build

ui-dev:
	cd ui && VITE_DEV_API_TARGET=http://127.0.0.1:$(API_PORT) npm run dev

ui-test:
	cd ui && npm run lint && npm run typecheck && npm test

ui-e2e:
	cd ui && npm run e2e
