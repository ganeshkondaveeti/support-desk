"""Weekly loop HTTP routes (architecture §11–12)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.auth import AdminActor
from app.api.deps import DbSession
from app.api.schemas import (
    WeeklyRunDetail,
    WeeklyRunListResponse,
    WeeklyRunRequest,
    WeeklyRunResponse,
)
from app.api.serializers import run_result_to_response, weekly_detail, weekly_summary
from app.config import get_settings
from app.db import models
from app.weekly.pipeline import WeeklyRunLockError, run_weekly

router = APIRouter(tags=["weekly"])


@router.post("/api/weekly/run", response_model=WeeklyRunResponse)
def post_weekly_run(
    actor: AdminActor,
    body: WeeklyRunRequest | None = None,
) -> WeeklyRunResponse:
    _ = actor
    payload = body or WeeklyRunRequest()
    settings = get_settings()
    as_of: date | None = payload.as_of
    reviews_path: Path | str | None = payload.reviews_path
    # reviews_path / as_of overrides are always accepted for the admin trigger
    # (eval FEAS-06); ignored by the cron path which calls run_weekly directly.
    if reviews_path and settings.app_env == "production":
        # In production, only allow paths under data/ to avoid arbitrary reads.
        path = Path(reviews_path)
        if ".." in path.parts or not str(path).startswith(("data/", "./data/")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="reviews_path not allowed",
            )

    try:
        result = run_weekly(
            as_of=as_of,
            trigger=payload.trigger,
            reviews_path=reviews_path,
        )
    except WeeklyRunLockError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "weekly run already in progress",
                "run_id": exc.run_id,
            },
        ) from exc

    return WeeklyRunResponse(**run_result_to_response(result))


@router.get("/api/weekly/runs", response_model=WeeklyRunListResponse)
def list_weekly_runs(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> WeeklyRunListResponse:
    total = int(session.scalar(select(func.count()).select_from(models.WeeklyRun)) or 0)
    rows = list(
        session.scalars(
            select(models.WeeklyRun)
            .order_by(models.WeeklyRun.started_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return WeeklyRunListResponse(
        items=[weekly_summary(r) for r in rows],
        total=total,
    )


@router.get("/api/weekly/runs/{run_id}", response_model=WeeklyRunDetail)
def get_weekly_run(run_id: str, session: DbSession) -> WeeklyRunDetail:
    run = session.get(models.WeeklyRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="weekly run not found")
    return weekly_detail(session, run)
