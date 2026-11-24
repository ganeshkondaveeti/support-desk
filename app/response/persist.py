"""Persist draft versions and propose reply_send after a graph pass."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bridge.canonical import record_reuse
from app.db import models
from app.db.repository import add
from app.gate import actions as gate_actions
from app.ids import safe_hex_id


def persist_graph_result(
    session: Session,
    ticket: models.Ticket,
    state: dict[str, Any],
    *,
    origin_override: str | None = None,
) -> models.DraftVersion | None:
    """
    Write the current draft version and update ticket status.

    On reviewer pass → ``awaiting_human`` + propose ``reply_send``.
    On blocked → ``blocked``, no reply_send.
    """
    draft = state.get("draft") or {}
    reviewer = state.get("reviewer") or {}
    reply_text = str(draft.get("reply_text") or "")
    if not reply_text:
        ticket.status = "blocked"
        ticket.rework_count = int(state.get("rework_count") or 0)
        session.flush()
        return None

    status = reviewer.get("status") or "fail"
    rework_count = int(state.get("rework_count") or 0)
    # Graph ends with fail only when rework already used → blocked.
    if status == "fail":
        final_status = "blocked"
        reviewer = {
            **reviewer,
            "status": "blocked",
            "flags": list(reviewer.get("flags") or reviewer.get("failures") or []),
        }
    elif status == "blocked":
        final_status = "blocked"
    else:
        final_status = "awaiting_human"

    origin = origin_override or str(draft.get("origin") or "orchestrator")
    version = _next_version(session, ticket.id)
    # Mark prior versions non-current.
    for old in session.scalars(
        select(models.DraftVersion).where(
            models.DraftVersion.ticket_id == ticket.id,
            models.DraftVersion.is_current.is_(True),
        )
    ).all():
        old.is_current = False

    routing = dict(draft.get("routing_note") or {})
    # Persist decision-table row for API/evals (FEAS-01); human edit also reads it.
    reviewer_payload = dict(reviewer)
    if draft.get("row") is not None:
        reviewer_payload["row"] = draft.get("row")
    dv = models.DraftVersion(
        # Avoid bare hex ids — pii.scan can false-positive on 10-digit runs.
        id=f"dv_{ticket.id}_v{version}",
        ticket_id=ticket.id,
        version=version,
        reply_text=reply_text,
        citation_url=draft.get("citation_url"),
        last_checked=draft.get("last_checked"),
        routing_note=routing,
        disagreement=draft.get("disagreement"),
        reviewer_result=reviewer_payload,
        origin=origin,
        is_current=True,
    )
    add(session, dv)

    ticket.status = final_status
    ticket.rework_count = rework_count
    if routing.get("priority"):
        ticket.priority = str(routing["priority"])
    if routing.get("team"):
        ticket.route_team = str(routing["team"])
    # Fingerprint may already be set by the orchestrator; keep whatever is on the ticket.
    session.flush()

    if origin == "canonical" and draft.get("canonical_id") and final_status == "awaiting_human":
        record_reuse(
            session,
            canonical_id=str(draft["canonical_id"]),
            ticket_id=ticket.id,
        )

    if final_status == "awaiting_human":
        _propose_reply_send(session, ticket, dv, reply_text)
    return dv


def _propose_reply_send(
    session: Session,
    ticket: models.Ticket,
    draft: models.DraftVersion,
    reply_text: str,
) -> models.ProposedAction:
    return gate_actions.propose(
        session,
        kind="reply_send",
        subject_type="ticket",
        subject_id=ticket.id,
        target={
            "ticket_id": ticket.id,
            "draft_version_id": draft.id,
            "channel": ticket.channel,
        },
        payload=reply_text,
    )


def _next_version(session: Session, ticket_id: str) -> int:
    rows = session.scalars(
        select(models.DraftVersion.version).where(models.DraftVersion.ticket_id == ticket_id)
    ).all()
    return (max(rows) + 1) if rows else 1


def write_ticket_audit(
    session: Session,
    *,
    actor: str,
    event: str,
    ticket_id: str,
    details: dict[str, Any],
) -> None:
    add(
        session,
        models.AuditEvent(
            id=safe_hex_id("ae"),
            ts=datetime.now(UTC),
            actor=actor,
            event=event,
            entity_type="ticket",
            entity_id=ticket_id,
            details=details,
        ),
    )
