"""Ticket queue routes (architecture §12)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.auth import AdminActor
from app.api.deps import DbSession
from app.api.schemas import (
    TicketCreateRequest,
    TicketCreateResponse,
    TicketDetailResponse,
    TicketEditRequest,
    TicketListResponse,
    TicketRefineRequest,
    TicketRejectRequest,
    TicketSeedResponse,
)
from app.api.serializers import ticket_detail, ticket_list_item
from app.api.text import redact_and_check
from app.api.worker import enqueue_seed_job
from app.db import models
from app.response.human import HumanActionError, edit_ticket, reject_ticket
from app.response.ingress import create_ticket
from app.response.refinement import RefinementError, refine_ticket

router = APIRouter(tags=["queue"])


@router.post("/api/tickets", response_model=TicketCreateResponse, status_code=201)
def post_ticket(
    body: TicketCreateRequest,
    session: DbSession,
    _actor: AdminActor,
) -> TicketCreateResponse:
    checked = redact_and_check(body.text, field="text")
    ticket = create_ticket(
        checked.text,
        channel=body.channel or "api",
        session=session,
        # Redaction already ran at the edge; pass on what it removed.
        known_pii_types=checked.types,
    )
    return TicketCreateResponse(
        id=ticket.id,
        status=ticket.status,
        pii_types_found=list(ticket.pii_types_found or []),
    )


@router.post("/api/tickets/seed", response_model=TicketSeedResponse, status_code=202)
async def post_tickets_seed(
    _actor: AdminActor,
) -> TicketSeedResponse:
    job_id, count = await enqueue_seed_job()
    return TicketSeedResponse(job_id=job_id, enqueued=count)


@router.get("/api/tickets", response_model=TicketListResponse)
def list_tickets(
    session: DbSession,
    status_filter: str | None = Query(default=None, alias="status"),
    team: str | None = None,
    priority: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> TicketListResponse:
    stmt = select(models.Ticket)
    count_stmt = select(func.count()).select_from(models.Ticket)
    if status_filter:
        stmt = stmt.where(models.Ticket.status == status_filter)
        count_stmt = count_stmt.where(models.Ticket.status == status_filter)
    if team:
        stmt = stmt.where(models.Ticket.route_team == team)
        count_stmt = count_stmt.where(models.Ticket.route_team == team)
    if priority:
        stmt = stmt.where(models.Ticket.priority == priority)
        count_stmt = count_stmt.where(models.Ticket.priority == priority)
    total = int(session.scalar(count_stmt) or 0)
    rows = list(
        session.scalars(
            stmt.order_by(models.Ticket.received_at.desc()).offset(offset).limit(limit)
        ).all()
    )
    return TicketListResponse(items=[ticket_list_item(t) for t in rows], total=total)


@router.get("/api/tickets/{ticket_id}", response_model=TicketDetailResponse)
def get_ticket(ticket_id: str, session: DbSession) -> TicketDetailResponse:
    ticket = session.get(models.Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return ticket_detail(session, ticket)


@router.post("/api/tickets/{ticket_id}/edit", response_model=TicketDetailResponse)
def post_ticket_edit(
    ticket_id: str,
    body: TicketEditRequest,
    session: DbSession,
    actor: AdminActor,
) -> TicketDetailResponse:
    checked = redact_and_check(body.text, field="text")
    try:
        edit_ticket(session, ticket_id, checked.text, actor, known_pii_types=checked.types)
    except HumanActionError as exc:
        code = 404 if exc.code == "NOT_FOUND" else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    ticket = session.get(models.Ticket, ticket_id)
    assert ticket is not None
    return ticket_detail(session, ticket)


@router.post("/api/tickets/{ticket_id}/reject", response_model=TicketDetailResponse)
def post_ticket_reject(
    ticket_id: str,
    session: DbSession,
    actor: AdminActor,
    body: TicketRejectRequest | None = None,
) -> TicketDetailResponse:
    _ = body  # reason accepted but not persisted (redacted data only in demo)
    try:
        reject_ticket(session, ticket_id, actor)
    except HumanActionError as exc:
        code = 404 if exc.code == "NOT_FOUND" else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    ticket = session.get(models.Ticket, ticket_id)
    assert ticket is not None
    return ticket_detail(session, ticket)


@router.post("/api/tickets/{ticket_id}/refine", response_model=TicketDetailResponse)
def post_ticket_refine(
    ticket_id: str,
    body: TicketRefineRequest,
    session: DbSession,
    actor: AdminActor,
) -> TicketDetailResponse:
    # Hint may be empty; when present, redact + length-check.
    hint = body.hint or ""
    if hint.strip():
        hint = redact_and_check(hint, field="hint").text
    else:
        hint = ""
    try:
        refine_ticket(session, ticket_id, body.kind, hint, actor=actor)
    except RefinementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    ticket = session.get(models.Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    return ticket_detail(session, ticket)
