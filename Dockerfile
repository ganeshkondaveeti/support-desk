# Skeleton Dockerfile (full multi-stage build lands in P11).
# Runtime: Python 3.11 + Node 22 for the MCP stdio server.

FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml README.md ./
COPY app ./app

# Editable install of the app package only; data/ is mounted or copied at deploy time.
RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV APP_ENV=production
ENV MCP_BACKEND=local

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
