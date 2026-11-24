"""HTTP API routers (architecture §12)."""

from __future__ import annotations

from fastapi import FastAPI

from app.api import approvals, audit, explainers, health, queue, sources, weekly
from app.api.errors import install_validation_handler


def mount_api(app: FastAPI) -> None:
    """Register all §12 routers and the G6 validation handler."""
    install_validation_handler(app)
    app.include_router(health.router)
    app.include_router(queue.router)
    app.include_router(weekly.router)
    app.include_router(sources.router)
    app.include_router(approvals.router)
    app.include_router(explainers.router)
    app.include_router(audit.router)
