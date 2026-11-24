"""First-boot seeding: weekly loop + seed_questions.json (architecture §16)."""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.db import models
from app.db.session import init_db, session_scope
from app.response.seed import seed_tickets
from app.weekly.pipeline import WeeklyRunLockError, run_weekly

logger = logging.getLogger(__name__)


def db_is_empty() -> bool:
    """True when there are no weekly runs and no tickets."""
    init_db()
    with session_scope() as session:
        runs = int(session.scalar(select(func.count()).select_from(models.WeeklyRun)) or 0)
        tickets = int(session.scalar(select(func.count()).select_from(models.Ticket)) or 0)
    return runs == 0 and tickets == 0


def bootstrap_if_empty(*, seed_limit: int | None = None) -> dict[str, object]:
    """
    On first boot with an empty DB: run the weekly loop once, then load seed
    questions through the normal ingress path.
    """
    init_db()
    if not db_is_empty():
        logger.info("bootstrap skipped (db not empty)")
        return {"skipped": True}

    logger.info("bootstrap starting (empty db)")
    weekly: dict[str, object] = {}
    try:
        result = run_weekly(trigger="manual")
        weekly = {
            "run_id": result.run_id,
            "status": result.status,
            "noop": result.noop,
        }
    except WeeklyRunLockError as exc:
        logger.warning("bootstrap weekly lock conflict run_id=%s", exc.run_id)
        weekly = {"status": "conflict", "run_id": exc.run_id}

    ticket_ids: list[str] = []
    with session_scope() as session:
        ticket_ids = seed_tickets(session, limit=seed_limit)

    logger.info(
        "bootstrap done weekly=%s tickets=%s",
        weekly.get("status"),
        len(ticket_ids),
    )
    return {
        "skipped": False,
        "weekly": weekly,
        "tickets": len(ticket_ids),
    }
