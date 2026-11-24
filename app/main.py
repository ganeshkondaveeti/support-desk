"""FastAPI app factory, API mount, scheduler, bootstrap, static UI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import Scope

from app.api import mount_api
from app.api.worker import get_worker, reset_worker
from app.bootstrap import bootstrap_if_empty
from app.config import get_settings
from app.corpus.loader import CorpusValidationError, load_corpus
from app.db.session import init_db
from app.guardrails.logging_filter import configure_logging
from app.response.faults import (
    check_fault_injection_on_startup,
    clear_eval_now,
    clear_ticket_fault,
    set_eval_now,
    set_ticket_fault,
)
from app.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


class SpaStaticFiles(StaticFiles):
    """Serve the UI build; unknown non-API paths fall back to index.html (P9.12)."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.startswith("api/") or "." in path.rsplit("/", 1)[-1]:
                raise
            return await super().get_response("index.html", scope)


class EvalHeadersMiddleware(BaseHTTPMiddleware):
    """Apply X-Eval-Ticket / X-Eval-Now for the request (APP_ENV=test only)."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        ticket = request.headers.get("X-Eval-Ticket")
        now = request.headers.get("X-Eval-Now")
        try:
            if ticket:
                try:
                    set_ticket_fault(ticket)
                except ValueError:
                    pass
            if now:
                try:
                    set_eval_now(now)
                except ValueError:
                    pass
            return await call_next(request)
        finally:
            clear_ticket_fault()
            clear_eval_now()


def create_app(
    *,
    load_facts: bool = True,
    enable_scheduler: bool = False,
    enable_bootstrap: bool = False,
    enable_worker: bool = False,
) -> FastAPI:
    """
    Build the FastAPI application.

    Scheduler, first-boot bootstrap and the seed worker are opt-in so tests stay
    fast. The module-level ``app`` (uvicorn target) turns them on.
    """
    settings = get_settings()
    configure_logging()
    check_fault_injection_on_startup(settings)

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

    # Ensure tables exist even when lifespan is not entered (e.g. some TestClient uses).
    init_db()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        bootstrap_task: asyncio.Task[None] | None = None
        if enable_bootstrap:
            # Background so /api/health responds during first-boot seeding.
            async def _bootstrap() -> None:
                try:
                    await asyncio.to_thread(bootstrap_if_empty)
                except Exception:  # noqa: BLE001
                    logger.exception("bootstrap failed")

            bootstrap_task = asyncio.create_task(_bootstrap(), name="bootstrap")
        if enable_worker:
            await get_worker().start()
        if enable_scheduler:
            start_scheduler(settings)
        try:
            yield
        finally:
            if enable_scheduler:
                stop_scheduler()
            if bootstrap_task is not None and not bootstrap_task.done():
                bootstrap_task.cancel()
                try:
                    await bootstrap_task
                except asyncio.CancelledError:
                    pass
            if enable_worker:
                await get_worker().stop()
                reset_worker()

    application = FastAPI(
        title="The Support Desk",
        description="Facts-only. No investment advice.",
        version="0.0.1",
        lifespan=lifespan,
    )
    application.add_middleware(EvalHeadersMiddleware)
    mount_api(application)

    # UI build (P9.12): app/static in the Docker image, else ui/dist in a checkout.
    here = Path(__file__).resolve().parent
    for static_dir in (here / "static", here.parent / "ui" / "dist"):
        if (static_dir / "index.html").is_file():
            application.mount("/", SpaStaticFiles(directory=static_dir, html=True), name="ui")
            break

    return application


# Uvicorn target: scheduler + first-boot seed + background worker (arch §11/§16).
app = create_app(enable_scheduler=True, enable_bootstrap=True, enable_worker=True)
