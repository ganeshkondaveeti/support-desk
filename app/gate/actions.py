"""ProposedAction lifecycle: propose → approve/reject → execute (architecture §10)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import models
from app.db.repository import add
from app.ids import safe_hex_display, safe_hex_id


class GateError(RuntimeError):
    """Illegal transition or missing action."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def payload_hash(payload: str | dict[str, Any]) -> str:
    raw = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def write_audit(
    session: Session,
    *,
    actor: str,
    event: str,
    entity_id: str,
    details: dict[str, Any],
) -> None:
    add(
        session,
        models.AuditEvent(
            id=safe_hex_id("ae"),
            ts=datetime.now(UTC),
            actor=actor,
            event=event,
            entity_type="proposed_action",
            entity_id=entity_id,
            details=details,
        ),
    )


def propose(
    session: Session,
    *,
    kind: str,
    subject_type: str,
    subject_id: str,
    target: dict[str, Any],
    payload: str,
) -> models.ProposedAction:
    """
    Insert a pending proposed action and freeze ``payload_sha256``.

    Re-proposing for the same subject+kind supersedes earlier *pending* actions
    (arch §10.1). Approved/executed/rejected rows are never touched.
    """
    if kind not in {"doc_append", "email_draft", "reply_send"}:
        raise GateError("INVALID_KIND", f"unknown action kind: {kind}")

    payload_sha256 = payload_hash(payload)

    # Session factory uses autoflush=False; flush so same-session re-proposes see
    # earlier pending rows.
    session.flush()
    existing = session.scalars(
        select(models.ProposedAction).where(
            models.ProposedAction.subject_type == subject_type,
            models.ProposedAction.subject_id == subject_id,
            models.ProposedAction.kind == kind,
            models.ProposedAction.status == "pending",
        )
    ).all()
    for row in existing:
        before = row.status
        row.status = "superseded"
        write_audit(
            session,
            actor="system",
            event="action_superseded",
            entity_id=row.id,
            details={"before": before, "after": "superseded", "reason": "repropose"},
        )

    action = models.ProposedAction(
        id=safe_hex_id("a"),
        kind=kind,
        subject_type=subject_type,
        subject_id=subject_id,
        target=target,
        payload=payload,
        payload_sha256=payload_sha256,
        status="pending",
    )
    add(session, action)
    write_audit(
        session,
        actor="system",
        event="action_proposed",
        entity_id=action.id,
        details={
            "kind": kind,
            "subject_type": subject_type,
            "subject_id": subject_id,
            # Break hex so pii.scan cannot false-positive PAN on sha256 (IGNORECASE).
            "payload_sha256": safe_hex_display(payload_sha256),
            "before": None,
            "after": "pending",
        },
    )
    return action


def approve(
    session: Session,
    action_id: str,
    actor: str,
    *,
    execute_fn: Callable[[Session, str], models.ProposedAction] | None = None,
) -> models.ProposedAction:
    """
    Approve one pending action and run the executor for that id only.

    ``execute_fn`` defaults to ``gate.executor.execute`` (injected in tests).
    """
    session.flush()
    action = session.get(models.ProposedAction, action_id)
    if action is None:
        raise GateError("NOT_FOUND", f"action {action_id} not found")
    if action.status != "pending":
        raise GateError(
            "INVALID_STATUS",
            f"action {action_id} is {action.status!r}; only pending can be approved",
        )

    before = action.status
    action.status = "approved"
    action.decided_by = actor
    action.decided_at = datetime.now(UTC)
    write_audit(
        session,
        actor=actor,
        event="action_approved",
        entity_id=action.id,
        details={"before": before, "after": "approved", "kind": action.kind},
    )
    session.flush()

    if execute_fn is None:
        from app.gate.executor import execute

        execute_fn = execute
    return execute_fn(session, action_id)


def reject(session: Session, action_id: str, actor: str) -> models.ProposedAction:
    """Reject one pending action. No side effects."""
    session.flush()
    action = session.get(models.ProposedAction, action_id)
    if action is None:
        raise GateError("NOT_FOUND", f"action {action_id} not found")
    if action.status != "pending":
        raise GateError(
            "INVALID_STATUS",
            f"action {action_id} is {action.status!r}; only pending can be rejected",
        )

    before = action.status
    action.status = "rejected"
    action.decided_by = actor
    action.decided_at = datetime.now(UTC)
    write_audit(
        session,
        actor=actor,
        event="action_rejected",
        entity_id=action.id,
        details={"before": before, "after": "rejected", "kind": action.kind},
    )
    session.flush()
    return action


def retry(session: Session, action_id: str, actor: str) -> models.ProposedAction:
    """Move a failed action back to pending so a human can approve again (§10.1)."""
    session.flush()
    action = session.get(models.ProposedAction, action_id)
    if action is None:
        raise GateError("NOT_FOUND", f"action {action_id} not found")
    if action.status != "failed":
        raise GateError(
            "INVALID_STATUS",
            f"action {action_id} is {action.status!r}; only failed can retry",
        )

    before = action.status
    action.status = "pending"
    action.decided_by = None
    action.decided_at = None
    action.executed_at = None
    action.result = None
    write_audit(
        session,
        actor=actor,
        event="action_retry",
        entity_id=action.id,
        details={"before": before, "after": "pending"},
    )
    session.flush()
    return action


def propose_weekly_actions(
    session: Session,
    *,
    pulse_id: str,
    pulse_body: str,
    explainer_id: str,
    explainer_body: str,
) -> list[models.ProposedAction]:
    """Four independent weekly actions (arch §7.8 / §10.2)."""
    settings = get_settings()
    docs_id = settings.google_docs_id or "local"
    emails = [e.strip() for e in settings.product_team_emails.split(",") if e.strip()]

    pulse_heading = "## Weekly pulse"
    explainer_heading = "## Fee explainer"
    pulse_subject = "Weekly product pulse"
    explainer_subject = "Fee explainer draft"

    actions = [
        propose(
            session,
            kind="doc_append",
            subject_type="pulse",
            subject_id=pulse_id,
            target={"document_id": docs_id, "heading": pulse_heading},
            payload=f"{pulse_heading}\n\n{pulse_body}",
        ),
        propose(
            session,
            kind="email_draft",
            subject_type="pulse",
            subject_id=pulse_id,
            target={"to": emails, "subject": pulse_subject},
            payload=pulse_body,
        ),
        propose(
            session,
            kind="doc_append",
            subject_type="fee_explainer",
            subject_id=explainer_id,
            target={"document_id": docs_id, "heading": explainer_heading},
            payload=f"{explainer_heading}\n\n{explainer_body}",
        ),
        propose(
            session,
            kind="email_draft",
            subject_type="fee_explainer",
            subject_id=explainer_id,
            target={"to": emails, "subject": explainer_subject},
            payload=explainer_body,
        ),
    ]
    return actions
