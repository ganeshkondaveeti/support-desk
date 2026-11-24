"""Test-only fault injection (architecture §16 G11, eval §2.4).

Honored only when ``APP_ENV=test``. Outside test, startup logs a refusal and
injection stays disabled.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import datetime
from typing import Literal

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

FaultName = Literal["uncited_once", "advice_always", "llm_down", "advice_ideas"]
KNOWN_FAULTS: frozenset[str] = frozenset(
    {"uncited_once", "advice_always", "llm_down", "advice_ideas"}
)

_TICKET_FAULT: ContextVar[str | None] = ContextVar("eval_ticket_fault", default=None)
_EVAL_NOW: ContextVar[datetime | None] = ContextVar("eval_now", default=None)
# Per-ticket first-pass tracking for uncited_once.
_UNCITED_FIRED: ContextVar[set[str]] = ContextVar("eval_uncited_fired", default=None)  # type: ignore[assignment]

_REFUSAL_LOGGED = False


def fault_injection_enabled(settings: Settings | None = None) -> bool:
    """True in APP_ENV=test when EVAL_FAULTS is set (including ``on``).

    ``on`` / ``true`` enables per-ticket headers without activating process-wide
    faults. Named values in EVAL_FAULTS activate those faults for every ticket.
    """
    cfg = settings or get_settings()
    if cfg.app_env != "test":
        return False
    return bool(cfg.eval_faults.strip())


def check_fault_injection_on_startup(settings: Settings | None = None) -> None:
    """G11: log refusal when EVAL_FAULTS is set outside APP_ENV=test."""
    global _REFUSAL_LOGGED
    cfg = settings or get_settings()
    if not cfg.eval_faults.strip():
        return
    if cfg.app_env == "test":
        logger.info("fault injection enabled faults=%s", sorted(_parse_faults(cfg.eval_faults)))
        return
    if not _REFUSAL_LOGGED:
        logger.warning(
            "fault injection refused (APP_ENV=%s)",
            cfg.app_env,
        )
        _REFUSAL_LOGGED = True


def active_faults(settings: Settings | None = None) -> frozenset[str]:
    """Process-wide faults from EVAL_FAULTS, or empty when not in test."""
    cfg = settings or get_settings()
    if cfg.app_env != "test":
        return frozenset()
    return _parse_faults(cfg.eval_faults)


def set_ticket_fault(name: str | None) -> None:
    """Apply ``X-Eval-Ticket`` for the current request (test only)."""
    if name is None or not name.strip():
        _TICKET_FAULT.set(None)
        return
    cleaned = name.strip()
    if cleaned not in KNOWN_FAULTS:
        raise ValueError(f"unknown eval fault: {cleaned!r}")
    cfg = get_settings()
    if cfg.app_env != "test":
        logger.warning("X-Eval-Ticket ignored (APP_ENV=%s)", cfg.app_env)
        _TICKET_FAULT.set(None)
        return
    _TICKET_FAULT.set(cleaned)


def clear_ticket_fault() -> None:
    _TICKET_FAULT.set(None)


def ticket_fault() -> str | None:
    return _TICKET_FAULT.get()


def set_eval_now(value: datetime | str | None) -> None:
    """Apply ``X-Eval-Now`` clock override (test only)."""
    if value is None:
        _EVAL_NOW.set(None)
        return
    cfg = get_settings()
    if cfg.app_env != "test":
        logger.warning("X-Eval-Now ignored (APP_ENV=%s)", cfg.app_env)
        _EVAL_NOW.set(None)
        return
    if isinstance(value, datetime):
        _EVAL_NOW.set(value)
        return
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    _EVAL_NOW.set(datetime.fromisoformat(text))


def clear_eval_now() -> None:
    _EVAL_NOW.set(None)


def eval_now() -> datetime | None:
    return _EVAL_NOW.get()


def fault_applies(name: FaultName, *, ticket_id: str | None = None) -> bool:
    """True when the named fault is active for this process or ticket.

    ``X-Eval-Ticket`` selects a single fault for the current request. When set,
    only that fault applies (process-wide EVAL_FAULTS are ignored for others).
    When unset, process-wide EVAL_FAULTS apply. ``ticket_id`` is reserved for
    future per-id scoping and is currently unused.
    """
    _ = ticket_id
    if get_settings().app_env != "test":
        return False
    ticket = ticket_fault()
    if ticket is not None:
        return ticket == name
    return name in active_faults()


def consume_uncited_once(ticket_id: str) -> bool:
    """
    Return True the first time ``uncited_once`` should fire for this ticket.

    Subsequent orchestrator passes (rework) return False so the draft cleans up.
    """
    if not fault_applies("uncited_once", ticket_id=ticket_id):
        return False
    fired = _UNCITED_FIRED.get()
    if fired is None:
        fired = set()
        _UNCITED_FIRED.set(fired)
    if ticket_id in fired:
        return False
    fired.add(ticket_id)
    return True


def reset_uncited_tracking() -> None:
    _UNCITED_FIRED.set(set())


def _parse_faults(raw: str) -> frozenset[str]:
    if not raw or not raw.strip():
        return frozenset()
    parts = {p.strip() for p in raw.replace(";", ",").split(",") if p.strip()}
    # EVAL_FAULTS=on / 1 / true means "hooks allowed" but activates no process-wide
    # fault. Named faults (llm_down, advice_always, …) must be listed explicitly.
    # Per-ticket faults still use X-Eval-Ticket under APP_ENV=test.
    if parts <= {"on", "1", "true", "yes"}:
        return frozenset()
    return frozenset(p for p in parts if p in KNOWN_FAULTS)
