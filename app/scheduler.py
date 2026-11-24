"""APScheduler weekly cron (architecture §11)."""

from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings, get_settings
from app.weekly.pipeline import WeeklyRunLockError, run_weekly

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def parse_weekly_cron(expr: str, *, timezone: str) -> CronTrigger:
    """Parse a 5-field crontab expression into a CronTrigger."""
    return CronTrigger.from_crontab(expr.strip(), timezone=timezone)


def build_scheduler(settings: Settings | None = None) -> AsyncIOScheduler:
    cfg = settings or get_settings()
    scheduler = AsyncIOScheduler(timezone=cfg.tz)
    trigger = parse_weekly_cron(cfg.weekly_cron, timezone=cfg.tz)
    scheduler.add_job(
        scheduled_weekly_run,
        trigger=trigger,
        id="weekly_loop",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    return scheduler


def scheduled_weekly_run() -> dict[str, Any]:
    """Cron entrypoint. Shares the DB run lock with the manual trigger."""
    logger.info("scheduler weekly trigger firing")
    try:
        result = run_weekly(trigger="cron")
    except WeeklyRunLockError as exc:
        logger.warning("scheduler weekly skipped lock run_id=%s", exc.run_id)
        return {"status": "conflict", "run_id": exc.run_id}
    logger.info(
        "scheduler weekly done run_id=%s status=%s noop=%s",
        result.run_id,
        result.status,
        result.noop,
    )
    return {
        "run_id": result.run_id,
        "status": result.status,
        "noop": result.noop,
    }


def start_scheduler(settings: Settings | None = None) -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    _scheduler = build_scheduler(settings)
    _scheduler.start()
    cfg = settings or get_settings()
    logger.info("scheduler started cron=%s tz=%s", cfg.weekly_cron, cfg.tz)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        if _scheduler.running:
            _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("scheduler stopped")


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler
