"""GET /api/audit — paginated audit events (architecture §12)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.api.schemas import AuditListResponse
from app.api.serializers import audit_out
from app.db import models

router = APIRouter(tags=["audit"])


@router.get("/api/audit", response_model=AuditListResponse)
def list_audit(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    entity_id: str | None = None,
    event: str | None = None,
) -> AuditListResponse:
    stmt = select(models.AuditEvent)
    count_stmt = select(func.count()).select_from(models.AuditEvent)
    if entity_id:
        stmt = stmt.where(models.AuditEvent.entity_id == entity_id)
        count_stmt = count_stmt.where(models.AuditEvent.entity_id == entity_id)
    if event:
        stmt = stmt.where(models.AuditEvent.event == event)
        count_stmt = count_stmt.where(models.AuditEvent.event == event)
    total = int(session.scalar(count_stmt) or 0)
    rows = list(
        session.scalars(
            stmt.order_by(models.AuditEvent.ts.desc()).offset(offset).limit(limit)
        ).all()
    )
    return AuditListResponse(
        items=[audit_out(e) for e in rows],
        total=total,
        offset=offset,
        limit=limit,
    )
