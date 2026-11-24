"""GET /api/health (architecture §12)."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select, text

from app.api.deps import DbSession
from app.api.schemas import HealthResponse
from app.api.serializers import weekly_summary
from app.corpus.loader import A_LAST_CHECKED, B_LAST_CHECKED, load_corpus
from app.db import models
from app.guardrails.allowlists import EDU_URLS, PUBLIC_DOMAINS

router = APIRouter(tags=["health"])


@router.get("/api/health", response_model=HealthResponse)
def health(session: DbSession) -> HealthResponse:
    corpus_loaded = False
    try:
        corpus = load_corpus()
        corpus_loaded = len(corpus.scheme_ids) >= 6
    except Exception:  # noqa: BLE001
        corpus_loaded = False

    db_reachable = False
    try:
        session.execute(text("SELECT 1"))
        db_reachable = True
    except Exception:  # noqa: BLE001
        db_reachable = False

    last_run = None
    if db_reachable:
        row = session.scalars(
            select(models.WeeklyRun).order_by(models.WeeklyRun.started_at.desc())
        ).first()
        if row is not None:
            last_run = weekly_summary(row)

    pending = 0
    ticket_count = 0
    if db_reachable:
        pending = int(
            session.scalar(
                select(func.count())
                .select_from(models.ProposedAction)
                .where(models.ProposedAction.status == "pending")
            )
            or 0
        )
        ticket_count = int(session.scalar(select(func.count()).select_from(models.Ticket)) or 0)

    status = "ok" if corpus_loaded and db_reachable else "degraded"
    return HealthResponse(
        status=status,
        corpus_loaded=corpus_loaded,
        db_reachable=db_reachable,
        last_weekly_run=last_run,
        pending_approvals=pending,
        gate_mode="full",
        seeded=ticket_count > 0,
        corpus_last_checked={"A": A_LAST_CHECKED, "B": B_LAST_CHECKED},
        public_domains=sorted(PUBLIC_DOMAINS),
        edu_urls=sorted(EDU_URLS),
    )
