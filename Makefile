# The Support Desk — local commands.

API_PORT ?= 8000
IMAGE ?= support-desk

.PHONY: help install dev test lint evals docker

help:
	@echo "install   uv sync + mcp-server npm ci/build"
	@echo "dev       uvicorn with reload on :$(API_PORT)"
	@echo "test      pytest"
	@echo "lint      ruff + mypy (guardrails/gate when present)"
	@echo "evals     python -m evals.run_all (P10)"
	@echo "docker    build the image"

install:
	uv sync
	cd mcp-server && npm ci && npm run build

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
	uv run pytest -q --cov=app/guardrails --cov-report=term-missing --cov-fail-under=90

evals:
	uv run python -m evals.run_all

docker:
	docker build -t $(IMAGE) .
