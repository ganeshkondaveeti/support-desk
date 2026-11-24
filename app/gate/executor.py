"""Execute exactly one approved ProposedAction (architecture §10.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any, Protocol

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.bridge.canonical import promote
from app.db import models
from app.db.repository import add
from app.gate.actions import GateError, payload_hash, write_audit
from app.gate.mcp_client import McpClient, McpClientError, get_mcp_client
from app.guardrails.pii import scan
from app.ids import safe_hex_id


class _McpPort(Protocol):
    def google_docs_append(
        self,
        *,
        document_id: str,
        content: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def gmail_draft(
        self,
        *,
        to: list[str],
        subject: str,
        body: str,
        idempotency_key: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]: ...


def execute(
    session: Session,
    action_id: str,
    *,
    mcp: _McpPort | None = None,
) -> models.ProposedAction:
    """
    Compare-and-set ``approved → executing``, re-check hash, last-mile PII scan,
    dispatch by kind, then ``executed`` / ``failed``.
    """
    cas = session.execute(
        update(models.ProposedAction)
        .where(
            models.ProposedAction.id == action_id,
            models.ProposedAction.status == "approved",
        )
        .values(status="executing")
    )
    if cas.rowcount != 1:  # type: ignore[attr-defined]
        action = session.get(models.ProposedAction, action_id)
        status = action.status if action is not None else "missing"
        raise GateError(
            "CAS_FAILED",
            f"execute CAS failed for {action_id} (status={status!r})",
        )
    session.flush()

    action = session.get(models.ProposedAction, action_id)
    assert action is not None

    current_hash = payload_hash(action.payload)
    if current_hash != action.payload_sha256:
        return _fail(
            session,
            action,
            code="HASH_MISMATCH",
            message="payload sha256 does not match frozen hash",
        )

    found = scan(action.payload)
    if found:
        return _fail(
            session,
            action,
            code="PII_BLOCK",
            message=f"payload contains PII: {found}",
            extra={"pii_types": found},
        )

    client: _McpPort = mcp if mcp is not None else get_mcp_client()

    try:
        result = _dispatch(session, action, client)
    except McpClientError as exc:
        return _fail(session, action, code="MCP_ERROR", message=str(exc))
    except GateError as exc:
        return _fail(session, action, code=exc.code, message=str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(session, action, code="EXEC_ERROR", message=str(exc))

    action.status = "executed"
    action.executed_at = datetime.now(UTC)
    action.result = result
    write_audit(
        session,
        actor=action.decided_by or "system",
        event="action_executed",
        entity_id=action.id,
        details={"before": "executing", "after": "executed", "result": result},
    )
    session.flush()
    return action


def _audit_safe(mcp_result: dict[str, Any]) -> dict[str, Any]:
    """
    Keep only the file *name* of a local-backend output path in stored results and
    audit rows. A full path adds nothing to the audit trail, and directory names
    (e.g. random temp dirs) can trip the PII guard and fail an approval.
    """
    out = dict(mcp_result)
    for key in ("path", "file", "file_path"):
        value = out.get(key)
        if isinstance(value, str) and value:
            out[key] = PurePath(value).name
    return out


def _dispatch(session: Session, action: models.ProposedAction, mcp: _McpPort) -> dict[str, Any]:
    if action.kind == "doc_append":
        return _doc_append(session, action, mcp)
    if action.kind == "email_draft":
        return _email_draft(action, mcp)
    if action.kind == "reply_send":
        return _reply_send(session, action)
    raise GateError("INVALID_KIND", f"cannot execute kind {action.kind!r}")


def _doc_append(
    session: Session,
    action: models.ProposedAction,
    mcp: _McpPort,
) -> dict[str, Any]:
    target = action.target or {}
    document_id = str(target.get("document_id") or "local")
    mcp_result = mcp.google_docs_append(
        document_id=document_id,
        content=action.payload,
        idempotency_key=action.id,
    )
    result: dict[str, Any] = {"mcp": _audit_safe(mcp_result)}
    if mcp_result.get("deduplicated"):
        result["deduplicated"] = True

    if action.subject_type == "fee_explainer":
        result.update(_promote_explainer(session, action))
    return result


def _promote_explainer(session: Session, action: models.ProposedAction) -> dict[str, Any]:
    """
    Hook: promote after successful fee-explainer doc_append (P4.6 / G8).

    Runs in the same DB transaction that marks the action executed. On failure,
    record ``promotion=pending``; MCP is never re-run.
    """
    try:
        outcome = promote(session, action.subject_id, action.id)
        if outcome.status == "noop":
            return {"promotion": "noop", "promotion_detail": outcome.detail}
        if outcome.status == "skipped":
            return {
                "promotion": f"skipped: {outcome.detail}",
                "canonical_id": outcome.canonical_id,
            }
        return {
            "promotion": outcome.status,
            "promotion_detail": outcome.detail,
            "canonical_id": outcome.canonical_id,
        }
    except Exception as exc:  # noqa: BLE001
        return {"promotion": "pending", "promotion_error": str(exc)}


def _email_draft(action: models.ProposedAction, mcp: _McpPort) -> dict[str, Any]:
    target = action.target or {}
    to_raw = target.get("to") or []
    to = [str(x) for x in to_raw] if isinstance(to_raw, list) else []
    subject = str(target.get("subject") or "Support Desk draft")
    mcp_result = mcp.gmail_draft(
        to=to,
        subject=subject,
        body=action.payload,
        idempotency_key=action.id,
    )
    result: dict[str, Any] = {"mcp": _audit_safe(mcp_result)}
    if mcp_result.get("deduplicated"):
        result["deduplicated"] = True
    return result


def _reply_send(session: Session, action: models.ProposedAction) -> dict[str, Any]:
    """Insert into outbox_replies. Only this module may write that table (P4.5 / P4.7)."""
    target = action.target or {}
    ticket_id = str(target.get("ticket_id") or "")
    draft_version_id = str(target.get("draft_version_id") or "")
    if not ticket_id or not draft_version_id:
        raise GateError(
            "INVALID_TARGET",
            "reply_send requires target.ticket_id and target.draft_version_id",
        )
    outbox_id = safe_hex_id("ob")
    add(
        session,
        models.OutboxReply(
            id=outbox_id,
            ticket_id=ticket_id,
            draft_version_id=draft_version_id,
            reply_text=action.payload,
            sent_at=datetime.now(UTC),
        ),
    )
    return {"outbox_id": outbox_id, "ticket_id": ticket_id, "draft_version_id": draft_version_id}


def _fail(
    session: Session,
    action: models.ProposedAction,
    *,
    code: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> models.ProposedAction:
    result: dict[str, Any] = {"error": code, "message": message}
    if extra:
        result.update(extra)
    action.status = "failed"
    action.executed_at = datetime.now(UTC)
    action.result = result
    write_audit(
        session,
        actor=action.decided_by or "system",
        event="action_failed",
        entity_id=action.id,
        details={"before": "executing", "after": "failed", "result": result},
    )
    session.flush()
    return action


def handshake(mcp: McpClient | None = None) -> list[str]:
    """List tools; used at startup (P11) and in tests. Asserts exactly two tools."""
    client = mcp if mcp is not None else get_mcp_client()
    names = client.list_tools()
    if set(names) != {"google_docs_append", "gmail_draft"}:
        raise McpClientError(f"expected exactly two tools; got {names}")
    if any("send" in n for n in names):
        raise McpClientError(f"send tool must not exist; got {names}")
    return names
