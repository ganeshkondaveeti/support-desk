"""FastAPI app factory, health endpoint, and static UI mount placeholder."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.guardrails.logging_filter import configure_logging


def create_app() -> FastAPI:
    get_settings()
    configure_logging()
    application = FastAPI(
        title="The Support Desk",
        description="Facts-only. No investment advice.",
        version="0.0.1",
    )

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    static_dir = Path(__file__).resolve().parent / "static"
    # Mount only when the UI build is present (P9 copies ui/dist here).
    if (static_dir / "index.html").is_file():
        application.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    return application


app = create_app()
