"""Ticket ingress: redact → persist → graph.invoke → draft + reply_send (arch §8.1)."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db import models, repository
from app.guardrails.pii import PII_TYPES, redact
from app.ids import safe_hex_id
from app.llm.client import LLMClient
from app.response.faults import eval_now
from app.response.graph import ainvoke_ticket, invoke_ticket
from app.response.persist import persist_graph_result

logger = logging.getLogger(__name__)

_DB_SESSION: ContextVar[Session | None] = ContextVar("response_db_session", default=None)


def current_db_session() -> Session | None:
    return _DB_SESSION.get()


def merge_pii_types(found: Iterable[str], known: Iterable[str]) -> list[str]:
    """
    Union of types found now and types an earlier redaction pass already removed.

    The API redacts at the edge (G6) before calling ingress, so re-redacting here
    finds only tokens. Without the earlier types, routing loses the PII bump and
    the Compliance agent never sees ``pii_detected``.
    """
    known_set = set(known)
    unknown = known_set - PII_TYPES
    if unknown:
        raise ValueError(f"unknown PII types: {sorted(unknown)}")
    return sorted(set(found) | known_set)


def create_ticket(
    text: str,
    channel: str = "api",
    *,
    session: Session,
    ticket_id: str | None = None,
    llm: LLMClient | None = None,
    compiled: Any = None,
    run_graph: bool = True,
    known_pii_types: Iterable[str] = (),
) -> models.Ticket:
    """Redact, insert ticket, run the response graph, persist outputs + draft."""
    if not isinstance(text, str):
        raise TypeError("create_ticket expects text as str")
    _ = llm
    redacted = redact(text)
    del text
    pii_types = merge_pii_types(redacted.types, known_pii_types)
    tid = ticket_id or safe_hex_id("tkt")
    now = _received_at_utc()
    ticket = models.Ticket(
        id=tid,
        received_at=now,
        channel=channel,
        question_redacted=redacted.text,
        pii_types_found=pii_types,
        fingerprint=None,
        status="processing",
        priority=None,
        route_team=None,
        rework_count=0,
        refinement_count=0,
    )
    repository.add(session, ticket)
    session.flush()

    if not run_graph:
        return ticket

    token = _DB_SESSION.set(session)
    try:
        state = invoke_ticket(
            ticket_id=tid,
            question=redacted.text,
            pii_types=pii_types,
            db_session=session,
            compiled=compiled,
        )
    finally:
        _DB_SESSION.reset(token)

    _persist_agent_outputs(session, tid, state)
    persist_graph_result(session, ticket, state)
    session.flush()
    logger.info(
        "ticket created id=%s status=%s pii=%s row=%s",
        tid,
        ticket.status,
        pii_types,
        (state.get("draft") or {}).get("row"),
    )
    return ticket


async def acreate_ticket(
    text: str,
    channel: str = "api",
    *,
    session: Session,
    ticket_id: str | None = None,
    compiled: Any = None,
    known_pii_types: Iterable[str] = (),
) -> models.Ticket:
    """Async ingress path (architecture §8.1 graph.ainvoke)."""
    if not isinstance(text, str):
        raise TypeError("acreate_ticket expects text as str")
    redacted = redact(text)
    del text
    pii_types = merge_pii_types(redacted.types, known_pii_types)
    tid = ticket_id or safe_hex_id("tkt")
    now = _received_at_utc()
    ticket = models.Ticket(
        id=tid,
        received_at=now,
        channel=channel,
        question_redacted=redacted.text,
        pii_types_found=pii_types,
        fingerprint=None,
        status="processing",
        priority=None,
        route_team=None,
        rework_count=0,
        refinement_count=0,
    )
    repository.add(session, ticket)
    session.flush()

    token = _DB_SESSION.set(session)
    try:
        state = await ainvoke_ticket(
            ticket_id=tid,
            question=redacted.text,
            pii_types=pii_types,
            db_session=session,
            compiled=compiled,
        )
    finally:
        _DB_SESSION.reset(token)

    _persist_agent_outputs(session, tid, state)
    persist_graph_result(session, ticket, state)
    session.flush()
    return ticket


def _received_at_utc() -> datetime:
    """Store ticket time in UTC (SQLite strips tzinfo; keep the wall clock as UTC)."""
    clock = eval_now() or datetime.now(tz=UTC)
    if clock.tzinfo is None:
        return clock.replace(tzinfo=UTC)
    return clock.astimezone(UTC)


def _persist_agent_outputs(session: Session, ticket_id: str, state: dict[str, Any]) -> None:
    runs = state.get("agent_runs") or {}
    now = datetime.now(tz=UTC)
    for agent in ("facts", "pattern", "compliance"):
        meta = runs.get(agent) or {}
        written = meta.get("written_output")
        if written is None:
            written = state.get(agent) or {}
        # Skip empty writes on refinements that did not re-run this agent.
        if not meta and not written:
            continue
        if not meta and agent not in (state.get("agent_runs") or {}):
            # On a fresh ticket every agent should have metrics; still persist state.
            pass
        row = models.AgentOutput(
            ticket_id=ticket_id,
            agent=agent,
            attempt=1,
            model_raw_choice=meta.get("model_raw_choice"),
            validated_choice=meta.get("validated_choice"),
            written_output=dict(written) if isinstance(written, dict) else {"value": written},
            latency_ms=meta.get("latency_ms"),
            created_at=now,
        )
        repository.add(session, row)
    session.flush()
