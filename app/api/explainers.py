"""GET /api/explainers/canonical (architecture §12)."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.schemas import CanonicalExplainersResponse
from app.api.serializers import canonical_out
from app.db import models

router = APIRouter(tags=["explainers"])


@router.get("/api/explainers/canonical", response_model=CanonicalExplainersResponse)
def get_canonical_explainers(session: DbSession) -> CanonicalExplainersResponse:
    rows = list(
        session.scalars(
            select(models.CanonicalExplainer).order_by(
                models.CanonicalExplainer.activated_at.desc()
            )
        ).all()
    )
    active = next((r for r in rows if r.active), None)
    history = [r for r in rows if not r.active or r is not active]
    return CanonicalExplainersResponse(
        active=canonical_out(session, active) if active else None,
        history=[canonical_out(session, r) for r in history if r is not active],
    )
