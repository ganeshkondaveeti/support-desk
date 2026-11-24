"""FastAPI app factory, health endpoint, and static UI mount placeholder."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.corpus.loader import CorpusValidationError, load_corpus
from app.guardrails.logging_filter import configure_logging

logger = logging.getLogger(__name__)


def create_app(*, load_facts: bool = True) -> FastAPI:
    get_settings()
    configure_logging()

    if load_facts:
        try:
            corpus = load_corpus()
            logger.info(
                "corpus loaded schemes=%s blocks=%s",
                len(corpus.scheme_ids),
                len(corpus.blocks),
            )
        except (CorpusValidationError, FileNotFoundError, OSError, ValueError) as exc:
            raise SystemExit(f"corpus validation failed: {exc}") from exc

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
