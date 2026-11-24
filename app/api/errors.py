"""Custom validation error handler (architecture §12 G6)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def install_validation_handler(app: FastAPI) -> None:
    """Drop ``input`` and ``ctx`` from 422 bodies so raw text never echoes."""

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # Never log the request body (G6).
        logger.info(
            "validation_error path=%s error_count=%s",
            request.url.path,
            len(exc.errors()),
        )
        safe: list[dict[str, object]] = []
        for err in exc.errors():
            safe.append(
                {
                    "loc": list(err.get("loc", ())),
                    "type": str(err.get("type", "value_error")),
                    "msg": "Invalid request",
                }
            )
        # Prefer the non-deprecated alias when present (Starlette ≥0.27).
        # Avoid evaluating HTTP_422_UNPROCESSABLE_ENTITY (deprecated accessor).
        code = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", None) or 422
        return JSONResponse(status_code=code, content={"detail": safe})
