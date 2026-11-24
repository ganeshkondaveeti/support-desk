"""PII-scrubbing log filter and JSON formatter (architecture §14)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.guardrails.pii import redact

_ATTACHED_LOGGERS = (
    "",  # root
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "httpx",
    "httpcore",
    "langgraph",
    "groq",
    "google_genai",
    "google.genai",
)


class PIIScrubFilter(logging.Filter):
    """Re-redact msg, args, and exc_text before emission."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg).text
        if record.args:
            if isinstance(record.args, dict):
                record.args = _scrub_args(record.args)
            else:
                record.args = _scrub_args(tuple(record.args))
        if record.exc_text:
            record.exc_text = redact(record.exc_text).text
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            # Replace the exception message in place when possible.
            try:
                scrubbed = redact(str(exc)).text
                if scrubbed != str(exc):
                    exc.args = (scrubbed, *exc.args[1:]) if exc.args else (scrubbed,)
            except Exception:  # noqa: BLE001 — never break logging
                pass
        return True


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("run_id", "ticket_id", "action_id", "node", "latency_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
            payload["exc"] = redact(payload["exc"]).text
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(*, level: int = logging.INFO) -> None:
    """Attach PIIScrubFilter and JSON formatting to root and provider loggers."""
    scrub = PIIScrubFilter()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(scrub)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)
    root.addFilter(scrub)

    for name in _ATTACHED_LOGGERS:
        if not name:
            continue
        logger = logging.getLogger(name)
        logger.addFilter(scrub)
        # Keep propagation so the root JSON handler formats once.
        logger.propagate = True


def _scrub_args(args: tuple[Any, ...] | dict[str, Any]) -> tuple[Any, ...] | dict[str, Any]:
    if isinstance(args, dict):
        return {key: _scrub_value(value) for key, value in args.items()}
    return tuple(_scrub_value(value) for value in args)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value).text
    return value
