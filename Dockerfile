# syntax=docker/dockerfile:1.7
# The Support Desk: one image, one service (arch §16, plan P11.1).
#   Stage 1 (node:22) builds the React UI and the TypeScript MCP server.
#   Stage 2 (python:3.11-slim) runs FastAPI + scheduler + LangGraph, with the Node 22
#   runtime so the executor can spawn the MCP server over stdio.

############################ 1. Node build ############################
FROM node:22-bookworm-slim AS node-build
WORKDIR /build

# MCP server: install, build, then keep production dependencies only.
COPY mcp-server/package.json mcp-server/package-lock.json mcp-server/
RUN cd mcp-server && npm ci --no-audit --no-fund
COPY mcp-server/ mcp-server/
RUN cd mcp-server && npm run build && npm prune --omit=dev

# UI: typecheck, build, and the Stitch content check (plan P9.11).
COPY ui/package.json ui/package-lock.json ui/
RUN cd ui && npm ci --no-audit --no-fund
COPY ui/ ui/
RUN cd ui && npm run build

############################ 2. Runtime ############################
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# libstdc++ for the Node binary; util-linux provides setpriv for the entrypoint.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 util-linux ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node 22 runtime only (no npm needed at runtime).
COPY --from=node-build /usr/local/bin/node /usr/local/bin/node
COPY --from=ghcr.io/astral-sh/uv:0.12.12 /uv /usr/local/bin/uv

WORKDIR /app

# Python dependencies first, for layer caching.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app/ app/
RUN uv sync --frozen --no-dev

# Read-only inputs: both corpus snapshots, reviews, baseline themes, seed questions.
# (data/raw and data/chroma_db are provenance / eval-only and stay out of the image.)
COPY data/parsed/ data/parsed/
COPY data/processed/ data/processed/
COPY data/normalized_reviews.json data/analyzed_themes.json data/seed_questions.json data/

COPY --from=node-build /build/mcp-server/dist mcp-server/dist
COPY --from=node-build /build/mcp-server/node_modules mcp-server/node_modules
COPY --from=node-build /build/mcp-server/package.json mcp-server/package.json
COPY --from=node-build /build/ui/dist app/static

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/state \
    && chown -R app:app /data/state

# Mutable state lives on the persistent disk (render.yaml mounts it at /data/state).
ENV APP_ENV=production \
    MCP_BACKEND=local \
    MCP_SERVER_CMD="node mcp-server/dist/index.js" \
    DATABASE_URL=sqlite:////data/state/support_desk.sqlite3 \
    LOCAL_DOC_PATH=/data/state/samples/appended_doc_entry.md \
    LOCAL_EMAIL_PATH=/data/state/samples/email_draft.md \
    TZ=Asia/Kolkata \
    LOG_TEXT=0 \
    PORT=8000

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# One worker: the scheduler, run lock and MCP stdio session are per process (arch §16).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT}\" --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
