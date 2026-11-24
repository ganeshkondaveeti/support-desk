"""Human actions: edit / reject / approve (architecture §8.7)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.db.repository import add
from app.gate import actions as gate_actions
from app.gate.actions import GateError
from app.guardrails.pii import redact
from app.response.persist import write_ticket_audit
from app.response.reviewer import review_draft


class HumanActionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def approve_ticket(
    session: Session,
    ticket_id: str,
    actor: str,
) -> models.ProposedAction:
    """Approve this ticket's single pending reply_send (delegates to gate.approve)."""
    ticket = session.get(models.Ticket, ticket_id)
    if ticket is None:
        raise HumanActionError("NOT_FOUND", f"ticket {ticket_id} not found")
    action = _pending_reply_send(session, ticket_id)
    if action is None:
        raise HumanActionError("NO_ACTION", f"ticket {ticket_id} has no pending reply_send")
    result = gate_actions.approve(session, action.id, actor)
    ticket.status = "sent" if result.status == "executed" else ticket.status
    if result.status == "failed":
        ticket.status = "awaiting_human"
    write_ticket_audit(
        session,
        actor=actor,
        event="ticket_approve",
        ticket_id=ticket_id,
        details={"action_id": action.id, "result_status": result.status},
    )
    session.flush()
    return result


def reject_ticket(
    session: Session,
    ticket_id: str,
    actor: str,
) -> models.Ticket:
    """Reject only this ticket and its pending reply_send."""
    ticket = session.get(models.Ticket, ticket_id)
    if ticket is None:
        raise HumanActionError("NOT_FOUND", f"ticket {ticket_id} not found")
    action = _pending_reply_send(session, ticket_id)
    if action is not None:
        gate_actions.reject(session, action.id, actor)
    before = ticket.status
    ticket.status = "rejected"
    write_ticket_audit(
        session,
        actor=actor,
        event="ticket_reject",
        ticket_id=ticket_id,
        details={"before": before, "after": "rejected", "action_id": action.id if action else None},
    )
    session.flush()
    return ticket


def edit_ticket(
    session: Session,
    ticket_id: str,
    new_text: str,
    actor: str,
    *,
    known_pii_types: Iterable[str] = (),
) -> tuple[models.DraftVersion, bool]:
    """
    Redact → new draft_versions row → reviewer.

    Pass: supersede + re-propose reply_send (creates first send on blocked / G9).
    Fail: failing version becomes current, supersede pending reply_send, status=blocked.
    Identical edit: no-op (returns current version, unchanged=True).
    Returns ``(draft_version, unchanged)``.
    """
    if not isinstance(new_text, str):
        raise TypeError("edit expects str")
    ticket = session.get(models.Ticket, ticket_id)
    if ticket is None:
        raise HumanActionError("NOT_FOUND", f"ticket {ticket_id} not found")
    if ticket.status == "rejected":
        raise HumanActionError("REJECTED", f"ticket {ticket_id} is rejected")

    redacted = redact(new_text)
    del new_text
    # The API redacts at the edge first; keep the types it found for the audit row.
    pii_type_names = sorted(set(redacted.types) | set(known_pii_types))
    current = _current_draft(session, ticket_id)
    if current is not None and current.reply_text == redacted.text:
        return current, True  # identical → no-op

    # Build a draft dict for the reviewer (facts from latest agent output if any).
    facts = _latest_facts(session, ticket_id)
    draft_payload: dict[str, Any] = {
        "reply_text": redacted.text,
        "citation_url": current.citation_url if current else None,
        "last_checked": current.last_checked if current else None,
        "row": (current.reviewer_result or {}).get("row") if current else None,
        "origin": "human_edit",
        "routing_note": dict(current.routing_note) if current else {},
        "disagreement": current.disagreement if current else None,
    }
    # Prefer structured fields from current draft if stored in routing/reviewer.
    if current is not None:
        draft_payload["citation_url"] = current.citation_url
        draft_payload["last_checked"] = current.last_checked

    reviewer = review_draft(draft_payload, facts=facts)
    was_blocked = ticket.status == "blocked"
    prior_flags = list((current.reviewer_result or {}).get("flags") or []) if current else []
    prior_row = draft_payload.get("row")

    version = _next_version(session, ticket_id)
    for old in session.scalars(
        select(models.DraftVersion).where(
            models.DraftVersion.ticket_id == ticket_id,
            models.DraftVersion.is_current.is_(True),
        )
    ).all():
        old.is_current = False

    reviewer_payload: dict[str, Any] = {
        **reviewer,
        "replaces_blocked": was_blocked,
        "prior_flags": prior_flags if was_blocked else [],
    }
    if prior_row is not None:
        reviewer_payload["row"] = prior_row

    dv = models.DraftVersion(
        id=f"dv_{ticket_id}_v{version}",
        ticket_id=ticket_id,
        version=version,
        reply_text=redacted.text,
        citation_url=draft_payload.get("citation_url"),
        last_checked=draft_payload.get("last_checked"),
        routing_note=dict(draft_payload.get("routing_note") or {}),
        disagreement=draft_payload.get("disagreement"),
        reviewer_result=reviewer_payload,
        origin="human_edit",
        is_current=True,
    )
    add(session, dv)
    session.flush()

    pending = _pending_reply_send(session, ticket_id)
    if reviewer["status"] == "pass":
        ticket.status = "awaiting_human"
        # Supersede old pending (propose does this) and create new reply_send.
        gate_actions.propose(
            session,
            kind="reply_send",
            subject_type="ticket",
            subject_id=ticket_id,
            target={
                "ticket_id": ticket_id,
                "draft_version_id": dv.id,
                "channel": ticket.channel,
                "replaces_blocked": was_blocked,
                "prior_flags": prior_flags if was_blocked else [],
            },
            payload=redacted.text,
        )
    else:
        # Fail: supersede any pending reply_send; leave ticket blocked.
        if pending is not None:
            before = pending.status
            pending.status = "superseded"
            gate_actions.write_audit(
                session,
                actor=actor,
                event="action_superseded",
                entity_id=pending.id,
                details={
                    "before": before,
                    "after": "superseded",
                    "reason": "human_edit_failed_review",
                },
            )
        ticket.status = "blocked"

    write_ticket_audit(
        session,
        actor=actor,
        event="ticket_edit",
        ticket_id=ticket_id,
        details={
            "draft_version_id": dv.id,
            "reviewer_status": reviewer["status"],
            "ticket_status": ticket.status,
            # Type names only (e.g. "PAN"), never raw values.
            "pii_type_names": pii_type_names,
        },
    )
    session.flush()
    return dv, False


def _pending_reply_send(session: Session, ticket_id: str) -> models.ProposedAction | None:
    return session.scalars(
        select(models.ProposedAction).where(
            models.ProposedAction.subject_type == "ticket",
            models.ProposedAction.subject_id == ticket_id,
            models.ProposedAction.kind == "reply_send",
            models.ProposedAction.status == "pending",
        )
    ).first()


def _current_draft(session: Session, ticket_id: str) -> models.DraftVersion | None:
    return session.scalars(
        select(models.DraftVersion).where(
            models.DraftVersion.ticket_id == ticket_id,
            models.DraftVersion.is_current.is_(True),
        )
    ).first()


def _next_version(session: Session, ticket_id: str) -> int:
    rows = session.scalars(
        select(models.DraftVersion.version).where(models.DraftVersion.ticket_id == ticket_id)
    ).all()
    return (max(rows) + 1) if rows else 1


def _latest_facts(session: Session, ticket_id: str) -> dict[str, Any] | None:
    row = session.scalars(
        select(models.AgentOutput)
        .where(
            models.AgentOutput.ticket_id == ticket_id,
            models.AgentOutput.agent == "facts",
        )
        .order_by(models.AgentOutput.id.desc())
    ).first()
    if row is None or not isinstance(row.written_output, dict):
        return None
    return dict(row.written_output)


__all__ = [
    "HumanActionError",
    "GateError",
    "approve_ticket",
    "reject_ticket",
    "edit_ticket",
]
