"""ORM → response model helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ActionOut,
    AgentOutputOut,
    AuditEventOut,
    CanonicalExplainerOut,
    DraftVersionOut,
    ExplainerOut,
    PulseOut,
    RefinementOut,
    ThemeOut,
    TicketDetailResponse,
    TicketListItem,
    WeeklyRunDetail,
    WeeklyRunSummary,
)
from app.db import models
from app.ids import safe_hex_display


def ticket_list_item(t: models.Ticket) -> TicketListItem:
    return TicketListItem(
        id=t.id,
        received_at=t.received_at,
        channel=t.channel,
        question_redacted=t.question_redacted,
        status=t.status,
        priority=t.priority,
        route_team=t.route_team,
        pii_types_found=list(t.pii_types_found or []),
        refinement_count=t.refinement_count,
        rework_count=t.rework_count,
        fingerprint=t.fingerprint,
    )


def draft_out(d: models.DraftVersion) -> DraftVersionOut:
    result = dict(d.reviewer_result or {})
    row_raw = result.get("row")
    row = int(row_raw) if isinstance(row_raw, int) else None
    return DraftVersionOut(
        id=d.id,
        version=d.version,
        reply_text=d.reply_text,
        citation_url=d.citation_url,
        last_checked=d.last_checked,
        routing_note=dict(d.routing_note or {}),
        disagreement=d.disagreement,
        reviewer_result=result,
        origin=d.origin,
        is_current=d.is_current,
        row=row,
    )


def agent_out(a: models.AgentOutput) -> AgentOutputOut:
    return AgentOutputOut(
        id=a.id,
        agent=a.agent,
        attempt=a.attempt,
        model_raw_choice=a.model_raw_choice,
        validated_choice=a.validated_choice,
        written_output=dict(a.written_output or {}),
        latency_ms=a.latency_ms,
        created_at=a.created_at,
    )


def refinement_out(r: models.Refinement) -> RefinementOut:
    return RefinementOut(
        id=r.id,
        n=r.n,
        kind=r.kind,
        hint_redacted=r.hint_redacted,
        nodes_rerun=list(r.nodes_rerun or []),
        created_at=r.created_at,
    )


def ticket_detail(
    session: Session,
    ticket: models.Ticket,
    *,
    unchanged: bool = False,
) -> TicketDetailResponse:
    drafts = list(
        session.scalars(
            select(models.DraftVersion)
            .where(models.DraftVersion.ticket_id == ticket.id)
            .order_by(models.DraftVersion.version.asc())
        ).all()
    )
    agents = list(
        session.scalars(
            select(models.AgentOutput)
            .where(models.AgentOutput.ticket_id == ticket.id)
            .order_by(models.AgentOutput.id.asc())
        ).all()
    )
    refinements = list(
        session.scalars(
            select(models.Refinement)
            .where(models.Refinement.ticket_id == ticket.id)
            .order_by(models.Refinement.n.asc())
        ).all()
    )
    pending = session.scalars(
        select(models.ProposedAction).where(
            models.ProposedAction.subject_type == "ticket",
            models.ProposedAction.subject_id == ticket.id,
            models.ProposedAction.kind == "reply_send",
            models.ProposedAction.status == "pending",
        )
    ).first()
    current = next((d for d in drafts if d.is_current), None)
    return TicketDetailResponse(
        id=ticket.id,
        received_at=ticket.received_at,
        channel=ticket.channel,
        question_redacted=ticket.question_redacted,
        status=ticket.status,
        priority=ticket.priority,
        route_team=ticket.route_team,
        pii_types_found=list(ticket.pii_types_found or []),
        fingerprint=ticket.fingerprint,
        rework_count=ticket.rework_count,
        refinement_count=ticket.refinement_count,
        current_draft=draft_out(current) if current else None,
        drafts=[draft_out(d) for d in drafts],
        agent_outputs=[agent_out(a) for a in agents],
        refinements=[refinement_out(r) for r in refinements],
        pending_reply_send_id=pending.id if pending else None,
        unchanged=unchanged,
    )


def weekly_summary(run: models.WeeklyRun, *, noop: bool = False) -> WeeklyRunSummary:
    return WeeklyRunSummary(
        id=run.id,
        as_of=run.as_of,
        status=run.status,
        trigger=run.trigger,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        noop=noop,
    )


def weekly_detail(session: Session, run: models.WeeklyRun) -> WeeklyRunDetail:
    themes = list(
        session.scalars(
            select(models.ThemeSnapshot).where(models.ThemeSnapshot.run_id == run.id)
        ).all()
    )
    pulse = session.scalars(select(models.Pulse).where(models.Pulse.run_id == run.id)).first()
    explainer = session.scalars(
        select(models.FeeExplainer).where(models.FeeExplainer.run_id == run.id)
    ).first()
    return WeeklyRunDetail(
        id=run.id,
        as_of=run.as_of,
        status=run.status,
        trigger=run.trigger,
        started_at=run.started_at,
        finished_at=run.finished_at,
        input_hash=safe_hex_display(run.input_hash),
        error=run.error,
        themes=[
            ThemeOut(
                theme_id=t.theme_id,
                name=t.name,
                description=t.description,
                review_ids=list(t.review_ids or []),
                count=t.count,
                avg_rating=t.avg_rating,
                score=t.score,
                window_start=t.window_start,
                window_end=t.window_end,
                trend=t.trend,
                weekly_counts=dict(t.weekly_counts or {}),
                is_top3=t.is_top3,
                is_fee_theme=t.is_fee_theme,
            )
            for t in themes
        ],
        pulse=(
            PulseOut(
                id=pulse.id,
                body_md=pulse.body_md,
                word_count=pulse.word_count,
                quotes=list(pulse.quotes or []),
                action_ideas=list(pulse.action_ideas or []),
                repeat_questions=list(pulse.repeat_questions or []),
                status=pulse.status,
            )
            if pulse
            else None
        ),
        explainer=(
            ExplainerOut(
                id=explainer.id,
                bullets=list(explainer.bullets or []),
                source_urls=list(explainer.source_urls or []),
                last_checked=explainer.last_checked,
                status=explainer.status,
            )
            if explainer
            else None
        ),
    )


def action_destination(action: models.ProposedAction) -> str:
    target = action.target or {}
    if action.kind == "doc_append":
        doc = target.get("document_id") or "local"
        heading = target.get("heading") or ""
        return f"Docs {doc} · {heading}".strip(" ·")
    if action.kind == "email_draft":
        to = target.get("to") or []
        to_s = ", ".join(to) if isinstance(to, list) else str(to)
        subject = target.get("subject") or ""
        return f"Email to {to_s or '(unset)'} · {subject}".strip(" ·")
    if action.kind == "reply_send":
        return f"Outbox · ticket {action.subject_id}"
    return action.kind


def what_else_happens(action: models.ProposedAction) -> str:
    if (
        action.kind == "doc_append"
        and action.subject_type == "fee_explainer"
        and action.status in {"pending", "approved", "executing"}
    ):
        return (
            f"Approving this makes Explainer #{action.subject_id} "
            "the canonical fee answer (promotes to canonical_explainers)."
        )
    if action.kind == "reply_send":
        return "Approving this writes one outbox reply for the ticket and marks it sent."
    if action.kind == "doc_append":
        return "Approving this appends the payload to the configured document once."
    if action.kind == "email_draft":
        return "Approving this creates one email draft (no send)."
    return ""


def action_out(action: models.ProposedAction) -> ActionOut:
    return ActionOut(
        id=action.id,
        kind=action.kind,
        subject_type=action.subject_type,
        subject_id=action.subject_id,
        target=dict(action.target or {}),
        payload=action.payload,
        payload_sha256=safe_hex_display(action.payload_sha256),
        status=action.status,
        decided_by=action.decided_by,
        decided_at=action.decided_at,
        executed_at=action.executed_at,
        result=dict(action.result) if isinstance(action.result, dict) else action.result,
        what_else_happens=what_else_happens(action),
        destination=action_destination(action),
    )


def audit_out(event: models.AuditEvent) -> AuditEventOut:
    return AuditEventOut(
        id=event.id,
        ts=event.ts,
        actor=event.actor,
        event=event.event,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        details=dict(event.details or {}),
    )


def canonical_out(
    session: Session,
    row: models.CanonicalExplainer,
) -> CanonicalExplainerOut:
    explainer = session.get(models.FeeExplainer, row.fee_explainer_id)
    reuse = list(
        session.scalars(
            select(models.AuditEvent)
            .where(
                models.AuditEvent.event == "canonical_reuse",
                models.AuditEvent.entity_type == "canonical_explainer",
                models.AuditEvent.entity_id == row.id,
            )
            .order_by(models.AuditEvent.ts.asc())
        ).all()
    )
    return CanonicalExplainerOut(
        id=row.id,
        fee_explainer_id=row.fee_explainer_id,
        theme_id=row.theme_id,
        covers=list(row.covers or []),
        approved_action_id=row.approved_action_id,
        active=row.active,
        activated_at=row.activated_at,
        superseded_at=row.superseded_at,
        bullets=list(explainer.bullets or []) if explainer else [],
        source_urls=list(explainer.source_urls or []) if explainer else [],
        last_checked=explainer.last_checked if explainer else None,
        reuse_log=[
            {
                "ticket_id": (e.details or {}).get("ticket_id"),
                "ts": e.ts.isoformat(),
                "origin": (e.details or {}).get("origin"),
            }
            for e in reuse
        ],
    )


def run_result_to_response(result: Any) -> dict[str, Any]:
    """Map WeeklyRunResult dataclass to a JSON-serializable dict."""
    return {
        "run_id": result.run_id,
        "as_of": result.as_of,
        "status": result.status,
        "noop": result.noop,
        "degraded": result.degraded,
        "theme_counts": dict(result.theme_counts or {}),
        "pulse_word_count": result.pulse_word_count,
        "explainer_bullets": result.explainer_bullets,
        "action_ids": list(result.action_ids or []),
        "error": result.error,
    }
