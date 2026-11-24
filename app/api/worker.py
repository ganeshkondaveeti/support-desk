"""Background ticket worker for bulk seed loads (architecture §11)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.db.session import session_scope
from app.ids import safe_hex_id
from app.response.ingress import create_ticket
from app.response.seed import load_seed_file

logger = logging.getLogger(__name__)

_CONCURRENCY = 4


@dataclass
class SeedJob:
    job_id: str
    items: list[dict[str, Any]]
    done: int = 0
    failed: int = 0
    status: str = "queued"
    ticket_ids: list[str] = field(default_factory=list)


class TicketWorker:
    """Asyncio queue worker with concurrency 4 for seed loads."""

    def __init__(self, *, concurrency: int = _CONCURRENCY) -> None:
        self.concurrency = concurrency
        self._queue: asyncio.Queue[SeedJob | None] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._jobs: dict[str, SeedJob] = {}
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for i in range(self.concurrency):
            self._tasks.append(asyncio.create_task(self._worker_loop(i), name=f"seed-{i}"))
        logger.info("ticket worker started concurrency=%s", self.concurrency)

    async def stop(self) -> None:
        if not self._started:
            return
        for _ in self._tasks:
            await self._queue.put(None)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False
        logger.info("ticket worker stopped")

    async def enqueue_seed(self, *, path: Any = None, limit: int | None = None) -> tuple[str, int]:
        items = load_seed_file(path)
        if limit is not None:
            items = items[:limit]
        job_id = safe_hex_id("seed")
        job = SeedJob(job_id=job_id, items=list(items), status="queued")
        self._jobs[job_id] = job
        await self._queue.put(job)
        return job_id, len(items)

    def get_job(self, job_id: str) -> SeedJob | None:
        return self._jobs.get(job_id)

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            job = await self._queue.get()
            try:
                if job is None:
                    return
                await self._process_job(job)
            finally:
                self._queue.task_done()

    async def _process_job(self, job: SeedJob) -> None:
        job.status = "running"
        logger.info("seed job start id=%s count=%s", job.job_id, len(job.items))
        # Process items sequentially within one worker pickup; concurrency comes
        # from multiple workers draining the queue. For a single seed job we
        # fan out with a semaphore so one enqueue uses up to concurrency slots.
        sem = asyncio.Semaphore(self.concurrency)

        async def _one(item: dict[str, Any]) -> None:
            async with sem:
                try:
                    await asyncio.to_thread(self._create_one, item, job)
                except Exception:  # noqa: BLE001
                    job.failed += 1
                    logger.exception("seed item failed job=%s", job.job_id)

        await asyncio.gather(*[_one(item) for item in job.items])
        job.status = "done"
        logger.info(
            "seed job done id=%s done=%s failed=%s",
            job.job_id,
            job.done,
            job.failed,
        )

    def _create_one(self, item: dict[str, Any], job: SeedJob) -> None:
        text = item["text"]
        with session_scope() as session:
            ticket = create_ticket(
                text,
                channel=str(item.get("channel") or "seed"),
                session=session,
                ticket_id=str(item["id"]) if item.get("id") else None,
            )
            job.ticket_ids.append(ticket.id)
            job.done += 1
        del text


_worker: TicketWorker | None = None


def get_worker() -> TicketWorker:
    global _worker
    if _worker is None:
        _worker = TicketWorker()
    return _worker


def reset_worker() -> None:
    global _worker
    _worker = None


async def enqueue_seed_job(*, path: Any = None, limit: int | None = None) -> tuple[str, int]:
    worker = get_worker()
    if not worker.started:
        await worker.start()
    return await worker.enqueue_seed(path=path, limit=limit)
