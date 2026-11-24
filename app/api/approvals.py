"""Proposed-action approval routes (architecture §10 / §12)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.auth import AdminActor
from app.api.deps import DbSession
from app.api.schemas import ActionListResponse, ActionOut
from app.api.serializers import action_out
from app.db import models
from app.gate.actions import GateError, approve, reject

router = APIRouter(tags=["approvals"])


@router.get("/api/actions", response_model=ActionListResponse)
def list_actions(
    session: DbSession,
    status_filter: str | None = Query(default="pending", alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ActionListResponse:
    stmt = select(models.ProposedAction)
    count_stmt = select(func.count()).select_from(models.ProposedAction)
    if status_filter:
        stmt = stmt.where(models.ProposedAction.status == status_filter)
        count_stmt = count_stmt.where(models.ProposedAction.status == status_filter)
    total = int(session.scalar(count_stmt) or 0)
    rows = list(
        session.scalars(
            stmt.order_by(models.ProposedAction.id.asc()).offset(offset).limit(limit)
        ).all()
    )
    return ActionListResponse(items=[action_out(a) for a in rows], total=total)


@router.get("/api/actions/{action_id}", response_model=ActionOut)
def get_action(action_id: str, session: DbSession) -> ActionOut:
    action = session.get(models.ProposedAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")
    return action_out(action)


@router.post("/api/actions/{action_id}/approve", response_model=ActionOut)
def post_approve(
    action_id: str,
    session: DbSession,
    actor: AdminActor,
) -> ActionOut:
    try:
        action = approve(session, action_id, actor)
    except GateError as exc:
        code = 404 if exc.code == "NOT_FOUND" else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return action_out(action)


@router.post("/api/actions/{action_id}/reject", response_model=ActionOut)
def post_reject(
    action_id: str,
    session: DbSession,
    actor: AdminActor,
) -> ActionOut:
    try:
        action = reject(session, action_id, actor)
    except GateError as exc:
        code = 404 if exc.code == "NOT_FOUND" else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return action_out(action)
