"""Scoped refinement ≤5 (architecture §8.7)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.db.repository import add
from app.guardrails.pii import redact
from app.ids import safe_hex_id
from app.response.graph import invoke_ticket
from app.response.ingress import _DB_SESSION
from app.response.persist import persist_graph_result, write_ticket_audit

RefineKind = Literal["shorter", "different_scheme", "check_fee_issue", "other"]
REFINE_KINDS: frozenset[str] = frozenset(
    {"shorter", "different_scheme", "check_fee_issue", "other"}
)
MAX_REFINEMENTS = 5

NODES_BY_KIND: dict[str, list[str]] = {
    "shorter": ["orchestrator", "reviewer"],
    "different_scheme": ["facts", "orchestrator", "reviewer"],
    "check_fee_issue": ["pattern", "orchestrator", "reviewer"],
    "other": ["orchestrator", "reviewer"],
}


class RefinementError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def refine_ticket(
    session: Session,
    ticket_id: str,
    kind: str,
    hint: str = "",
    *,
    actor: str = "reviewer",
    compiled: Any = None,
) -> models.Ticket:
    """
    Apply one scoped refinement. Raises RefinementError with status_code=409
    when the cap of 5 is reached.
    """
    if kind not in REFINE_KINDS:
        raise RefinementError("INVALID_KIND", f"unknown refine kind: {kind!r}")
    ticket = session.get(models.Ticket, ticket_id)
    if ticket is None:
        raise RefinementError("NOT_FOUND", f"ticket {ticket_id} not found")
    if ticket.refinement_count >= MAX_REFINEMENTS:
        raise RefinementError(
            "refinement_cap_reached",
            f"ticket {ticket_id} already has {MAX_REFINEMENTS} refinements",
            status_code=409,
        )

    redacted_hint = redact(hint or "").text
    n = ticket.refinement_count + 1
    nodes = list(NODES_BY_KIND[kind])

    token = _DB_SESSION.set(session)
    try:
        state = invoke_ticket(
            ticket_id=ticket_id,
            question=ticket.question_redacted,
            pii_types=list(ticket.pii_types_found or []),
            refine_kind=kind,
            refine_hint=redacted_hint or None,
            refinement_count=n,
            db_session=session,
            compiled=compiled,
        )
    finally:
        _DB_SESSION.reset(token)

    # Persist only agents that re-ran.
    _persist_refinement_outputs(session, ticket_id, state, kind=kind, n=n)

    ticket.refinement_count = n
    # Each refinement gets its own rework allowance — rework_count comes from state.
    ticket.rework_count = int(state.get("rework_count") or 0)
    persist_graph_result(
        session,
        ticket,
        state,
        origin_override="refinement",
    )

    ref = models.Refinement(
        id=safe_hex_id("rf"),
        ticket_id=ticket_id,
        n=n,
        kind=kind,
        hint_redacted=redacted_hint,
        nodes_rerun=nodes,
        created_at=datetime.now(UTC),
    )
    add(session, ref)
    write_ticket_audit(
        session,
        actor=actor,
        event="ticket_refine",
        ticket_id=ticket_id,
        details={"n": n, "kind": kind, "nodes_rerun": nodes},
    )
    session.flush()
    return ticket


def _persist_refinement_outputs(
    session: Session,
    ticket_id: str,
    state: dict[str, Any],
    *,
    kind: str,
    n: int,
) -> None:
    """Write agent_outputs only for agents that re-ran."""
    rerun_agents = {
        "different_scheme": ("facts",),
        "check_fee_issue": ("pattern",),
    }.get(kind, ())
    if not rerun_agents:
        return
    runs = state.get("agent_runs") or {}
    now = datetime.now(UTC)
    for agent in rerun_agents:
        meta = runs.get(agent) or {}
        written = meta.get("written_output") or state.get(agent) or {}
        # attempt = refinement n (orchestrator rework uses attempt on same pass)
        existing = session.scalars(
            select(models.AgentOutput).where(
                models.AgentOutput.ticket_id == ticket_id,
                models.AgentOutput.agent == agent,
            )
        ).all()
        attempt = len(existing) + 1
        add(
            session,
            models.AgentOutput(
                ticket_id=ticket_id,
                agent=agent,
                attempt=attempt,
                model_raw_choice=meta.get("model_raw_choice"),
                validated_choice=meta.get("validated_choice"),
                written_output=dict(written) if isinstance(written, dict) else {"value": written},
                latency_ms=meta.get("latency_ms"),
                created_at=now,
            ),
        )
    _ = n
    session.flush()
