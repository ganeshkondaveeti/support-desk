"""Approval gate + executor (P4)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from app.db import models
from app.db.repository import add
from app.db.session import init_db, reset_engine, session_scope
from app.db.snapshots import diff, snapshot
from app.gate.actions import (
    GateError,
    approve,
    payload_hash,
    propose,
    propose_weekly_actions,
    reject,
    retry,
)
from app.gate.executor import execute, handshake
from app.gate.mcp_client import McpClientError, reset_mcp_client
from sqlalchemy import select


class FakeMcp:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.mails: list[dict[str, Any]] = []

    def google_docs_append(
        self,
        *,
        document_id: str,
        content: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        for prior in self.docs:
            if prior["idempotency_key"] == idempotency_key:
                return {"deduplicated": True, "documentId": document_id}
        self.docs.append(
            {
                "document_id": document_id,
                "content": content,
                "idempotency_key": idempotency_key,
            }
        )
        return {"documentId": document_id, "deduplicated": False}

    def gmail_draft(
        self,
        *,
        to: list[str],
        subject: str,
        body: str,
        idempotency_key: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        for prior in self.mails:
            if prior["idempotency_key"] == idempotency_key:
                return {"deduplicated": True, "draftId": "dup"}
        self.mails.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
                "idempotency_key": idempotency_key,
                "cc": cc or [],
                "bcc": bcc or [],
            }
        )
        return {"draftId": "d1", "deduplicated": False}


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    reset_engine()
    reset_mcp_client()
    url = f"sqlite:///{tmp_path / 'gate.sqlite3'}"
    init_db(url)
    yield url
    reset_mcp_client()
    reset_engine()


def _propose_doc(
    session: Any,
    *,
    subject_id: str = "p_1",
    payload: str = "## Pulse\n\nbody",
) -> models.ProposedAction:
    return propose(
        session,
        kind="doc_append",
        subject_type="pulse",
        subject_id=subject_id,
        target={"document_id": "local", "heading": "## Pulse"},
        payload=payload,
    )


def _approve(session: Any, action_id: str, mcp: FakeMcp) -> models.ProposedAction:
    return approve(
        session,
        action_id,
        "tester",
        execute_fn=lambda s, i: execute(s, i, mcp=mcp),
    )


def test_approve_only_changes_one_action(db_url: str) -> None:
    fake = FakeMcp()
    with session_scope(db_url) as session:
        a = _propose_doc(session, subject_id="p_a", payload="## A\n\none")
        b = _propose_doc(session, subject_id="p_b", payload="## B\n\ntwo")
        before = snapshot(session)
        a_id, b_id = a.id, b.id

    with session_scope(db_url) as session:
        _approve(session, a_id, fake)
        after = snapshot(session)
        delta = diff(before, after)

    with session_scope(db_url) as session:
        row_a = session.get(models.ProposedAction, a_id)
        row_b = session.get(models.ProposedAction, b_id)
        assert row_a is not None and row_a.status == "executed"
        assert row_b is not None and row_b.status == "pending"

    changed_actions = delta.changed.get("proposed_actions", [])
    changed_ids = {c["after"]["id"] for c in changed_actions}
    assert a_id in changed_ids
    assert b_id not in changed_ids
    assert len(fake.docs) == 1


def test_approve_twice_executes_once(db_url: str) -> None:
    fake = FakeMcp()
    with session_scope(db_url) as session:
        action = _propose_doc(session)
        action_id = action.id

    with session_scope(db_url) as session:
        _approve(session, action_id, fake)

    with session_scope(db_url) as session:
        with pytest.raises(GateError) as exc:
            _approve(session, action_id, fake)
        assert exc.value.code == "INVALID_STATUS"

    assert len(fake.docs) == 1


def test_tampered_payload_refused(db_url: str) -> None:
    fake = FakeMcp()
    with session_scope(db_url) as session:
        action = _propose_doc(session, payload="## Pulse\n\nclean")
        action_id = action.id
        # Tamper without updating the frozen hash.
        action.payload = "## Pulse\n\ntampered"

    with session_scope(db_url) as session:
        # Status is still pending; approve then execute sees hash mismatch.
        row = session.get(models.ProposedAction, action_id)
        assert row is not None
        row.payload = "## Pulse\n\ntampered"
        result = _approve(session, action_id, fake)
        assert result.status == "failed"
        assert result.result is not None
        assert result.result["error"] == "HASH_MISMATCH"

    assert fake.docs == []


def test_pii_payload_ends_failed(db_url: str) -> None:
    fake = FakeMcp()
    # Bypass repository add() PII scan: propose clean, then overwrite payload and
    # re-hash so the last-mile executor scan is what fails (not HASH_MISMATCH).
    planted = "## Pulse\n\nMy PAN is ABCDE1234F"
    with session_scope(db_url) as session:
        action = _propose_doc(session, payload="## Pulse\n\nclean text")
        action_id = action.id
        action.payload = planted
        action.payload_sha256 = payload_hash(planted)

    with session_scope(db_url) as session:
        result = _approve(session, action_id, fake)
        assert result.status == "failed"
        assert result.result is not None
        assert result.result["error"] == "PII_BLOCK"

    assert fake.docs == []


def test_reject_leaves_others_pending(db_url: str) -> None:
    with session_scope(db_url) as session:
        a = _propose_doc(session, subject_id="p_a")
        b = _propose_doc(session, subject_id="p_b")
        a_id, b_id = a.id, b.id

    with session_scope(db_url) as session:
        reject(session, a_id, "tester")

    with session_scope(db_url) as session:
        assert session.get(models.ProposedAction, a_id).status == "rejected"  # type: ignore[union-attr]
        assert session.get(models.ProposedAction, b_id).status == "pending"  # type: ignore[union-attr]


def test_repropose_supersedes_pending(db_url: str) -> None:
    with session_scope(db_url) as session:
        first = _propose_doc(session, subject_id="p_1", payload="## Pulse\n\nv1")
        first_id = first.id
        second = _propose_doc(session, subject_id="p_1", payload="## Pulse\n\nv2")
        second_id = second.id

    with session_scope(db_url) as session:
        assert session.get(models.ProposedAction, first_id).status == "superseded"  # type: ignore[union-attr]
        assert session.get(models.ProposedAction, second_id).status == "pending"  # type: ignore[union-attr]


def test_reply_send_writes_outbox(db_url: str) -> None:
    fake = FakeMcp()
    with session_scope(db_url) as session:
        add(
            session,
            models.Ticket(
                id="t_1",
                received_at=datetime.now(UTC),
                channel="web",
                question_redacted="What is exit load?",
                pii_types_found=[],
                status="awaiting_human",
                rework_count=0,
                refinement_count=0,
            ),
        )
        add(
            session,
            models.DraftVersion(
                id="dv_1",
                ticket_id="t_1",
                version=1,
                reply_text="Exit load is 1%.",
                citation_url="https://example.com",
                last_checked="01-Sep-2026",
                routing_note={},
                reviewer_result={"status": "pass"},
                origin="orchestrator",
                is_current=True,
            ),
        )
        action = propose(
            session,
            kind="reply_send",
            subject_type="ticket",
            subject_id="t_1",
            target={"ticket_id": "t_1", "draft_version_id": "dv_1", "channel": "web"},
            payload="Exit load is 1%.",
        )
        action_id = action.id

    with session_scope(db_url) as session:
        result = _approve(session, action_id, fake)
        assert result.status == "executed"

    with session_scope(db_url) as session:
        rows = session.scalars(select(models.OutboxReply)).all()
        assert len(rows) == 1
        assert rows[0].ticket_id == "t_1"
        assert rows[0].reply_text == "Exit load is 1%."


def test_fee_explainer_doc_append_records_promotion_hook(db_url: str) -> None:
    fake = FakeMcp()
    with session_scope(db_url) as session:
        action = propose(
            session,
            kind="doc_append",
            subject_type="fee_explainer",
            subject_id="fe_1",
            target={"document_id": "local", "heading": "## Fee explainer"},
            payload="## Fee explainer\n\n- Exit load: 1%.",
        )
        action_id = action.id

    with session_scope(db_url) as session:
        result = _approve(session, action_id, fake)
        assert result.status == "executed"
        assert result.result is not None
        assert result.result.get("promotion") == "noop"


def test_approve_rejects_unknown_and_non_pending(db_url: str) -> None:
    fake = FakeMcp()
    with session_scope(db_url) as session:
        with pytest.raises(GateError) as exc:
            _approve(session, "a_missing", fake)
        assert exc.value.code == "NOT_FOUND"

    with session_scope(db_url) as session:
        action = _propose_doc(session)
        action_id = action.id
        reject(session, action_id, "tester")

    with session_scope(db_url) as session:
        with pytest.raises(GateError) as exc:
            _approve(session, action_id, fake)
        assert exc.value.code == "INVALID_STATUS"


def test_reject_unknown_and_retry_flow(db_url: str) -> None:
    fake = FakeMcp()
    with session_scope(db_url) as session:
        with pytest.raises(GateError) as exc:
            reject(session, "a_missing", "tester")
        assert exc.value.code == "NOT_FOUND"

    with session_scope(db_url) as session:
        action = _propose_doc(session, payload="## Pulse\n\nclean")
        action_id = action.id
        action.payload = "## Pulse\n\ntampered"

    with session_scope(db_url) as session:
        row = session.get(models.ProposedAction, action_id)
        assert row is not None
        row.payload = "## Pulse\n\ntampered"
        failed = _approve(session, action_id, fake)
        assert failed.status == "failed"

    with session_scope(db_url) as session:
        with pytest.raises(GateError) as exc:
            retry(session, "a_missing", "tester")
        assert exc.value.code == "NOT_FOUND"
        with pytest.raises(GateError) as exc:
            # still failed until we retry; reject of failed is invalid
            reject(session, action_id, "tester")
        assert exc.value.code == "INVALID_STATUS"
        reopened = retry(session, action_id, "tester")
        assert reopened.status == "pending"
        # restore payload so a second approve can succeed
        reopened.payload = "## Pulse\n\nclean"
        reopened.payload_sha256 = payload_hash(reopened.payload)

    with session_scope(db_url) as session:
        # pending again after retry, but payload was restored only in prior session —
        # re-load and fix if needed
        row = session.get(models.ProposedAction, action_id)
        assert row is not None
        assert row.status == "pending"
        row.payload = "## Pulse\n\nclean"
        row.payload_sha256 = payload_hash(row.payload)
        ok = _approve(session, action_id, fake)
        assert ok.status == "executed"

    with session_scope(db_url) as session:
        with pytest.raises(GateError) as exc:
            retry(session, action_id, "tester")
        assert exc.value.code == "INVALID_STATUS"


def test_email_draft_executes(db_url: str) -> None:
    fake = FakeMcp()
    with session_scope(db_url) as session:
        action = propose(
            session,
            kind="email_draft",
            subject_type="pulse",
            subject_id="p_mail",
            target={"to": ["product@example.com"], "subject": "Pulse"},
            payload="Body of pulse",
        )
        action_id = action.id

    with session_scope(db_url) as session:
        result = _approve(session, action_id, fake)
        assert result.status == "executed"
        assert len(fake.mails) == 1
        assert fake.mails[0]["subject"] == "Pulse"


def test_mcp_error_marks_failed(db_url: str) -> None:
    class BoomMcp(FakeMcp):
        def google_docs_append(self, **kwargs: Any) -> dict[str, Any]:
            raise McpClientError("boom")

    boom = BoomMcp()
    with session_scope(db_url) as session:
        action = _propose_doc(session)
        action_id = action.id

    with session_scope(db_url) as session:
        result = _approve(session, action_id, boom)
        assert result.status == "failed"
        assert result.result is not None
        assert result.result["error"] == "MCP_ERROR"


def test_reply_send_invalid_target(db_url: str) -> None:
    fake = FakeMcp()
    with session_scope(db_url) as session:
        action = propose(
            session,
            kind="reply_send",
            subject_type="ticket",
            subject_id="t_bad",
            target={"channel": "web"},
            payload="hello",
        )
        action_id = action.id

    with session_scope(db_url) as session:
        result = _approve(session, action_id, fake)
        assert result.status == "failed"
        assert result.result is not None
        assert result.result["error"] == "INVALID_TARGET"


def test_propose_weekly_actions_four_kinds(db_url: str) -> None:
    with session_scope(db_url) as session:
        actions = propose_weekly_actions(
            session,
            pulse_id="p_w",
            pulse_body="pulse body",
            explainer_id="fe_w",
            explainer_body="explainer body",
        )
        assert len(actions) == 4
        kinds = {(a.kind, a.subject_type) for a in actions}
        assert kinds == {
            ("doc_append", "pulse"),
            ("email_draft", "pulse"),
            ("doc_append", "fee_explainer"),
            ("email_draft", "fee_explainer"),
        }


def test_propose_invalid_kind(db_url: str) -> None:
    with session_scope(db_url) as session:
        with pytest.raises(GateError) as exc:
            propose(
                session,
                kind="send_email",
                subject_type="pulse",
                subject_id="p_x",
                target={},
                payload="x",
            )
        assert exc.value.code == "INVALID_KIND"


def test_execute_cas_fails_when_not_approved(db_url: str) -> None:
    fake = FakeMcp()
    with session_scope(db_url) as session:
        action = _propose_doc(session)
        action_id = action.id

    with session_scope(db_url) as session:
        with pytest.raises(GateError) as exc:
            execute(session, action_id, mcp=fake)
        assert exc.value.code == "CAS_FAILED"


def test_promotion_pending_when_promote_raises(
    db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMcp()

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr("app.gate.executor.promote", boom)
    with session_scope(db_url) as session:
        action = propose(
            session,
            kind="doc_append",
            subject_type="fee_explainer",
            subject_id="fe_boom",
            target={"document_id": "local", "heading": "## Fee"},
            payload="## Fee\n\nbullet",
        )
        action_id = action.id

    with session_scope(db_url) as session:
        result = _approve(session, action_id, fake)
        assert result.status == "executed"
        assert result.result is not None
        assert result.result.get("promotion") == "pending"


def test_promotion_skipped_and_promoted_statuses(
    db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bridge.canonical import PromoteResult

    fake = FakeMcp()
    monkeypatch.setattr(
        "app.gate.executor.promote",
        lambda *_a, **_k: PromoteResult(
            status="skipped",
            detail="older than active",
            canonical_id="c_old",
        ),
    )
    with session_scope(db_url) as session:
        action = propose(
            session,
            kind="doc_append",
            subject_type="fee_explainer",
            subject_id="fe_skip",
            target={"document_id": "local", "heading": "## Fee"},
            payload="## Fee\n\nbullet",
        )
        action_id = action.id
    with session_scope(db_url) as session:
        result = _approve(session, action_id, fake)
        assert result.result is not None
        assert "skipped" in str(result.result.get("promotion"))

    monkeypatch.setattr(
        "app.gate.executor.promote",
        lambda *_a, **_k: PromoteResult(
            status="promoted",
            detail="ok",
            canonical_id="c_new",
        ),
    )
    with session_scope(db_url) as session:
        action = propose(
            session,
            kind="doc_append",
            subject_type="fee_explainer",
            subject_id="fe_ok",
            target={"document_id": "local", "heading": "## Fee"},
            payload="## Fee\n\nbullet2",
        )
        action_id = action.id
    with session_scope(db_url) as session:
        result = _approve(session, action_id, fake)
        assert result.result is not None
        assert result.result.get("promotion") == "promoted"


def test_email_deduplicated_flag(db_url: str) -> None:
    fake = FakeMcp()
    with session_scope(db_url) as session:
        action = propose(
            session,
            kind="email_draft",
            subject_type="pulse",
            subject_id="p_dedup",
            target={"to": [], "subject": "S"},
            payload="Body",
        )
        action_id = action.id
    with session_scope(db_url) as session:
        first = _approve(session, action_id, fake)
        assert first.status == "executed"
        # Force a second execute path with same mcp key via CAS on a new approved row
        # by proposing and approving again after supersede — call gmail twice with same key.
        fake.gmail_draft(
            to=[],
            subject="S",
            body="Body",
            idempotency_key=action_id,
        )
        # Direct dispatch of dedup path: re-call through execute after planting approved
        action2 = propose(
            session,
            kind="email_draft",
            subject_type="pulse",
            subject_id="p_dedup2",
            target={"to": [], "subject": "S"},
            payload="Body",
        )
        # Pre-seed fake so the next call de-dups
        fake.mails.append(
            {
                "to": [],
                "subject": "S",
                "body": "Body",
                "idempotency_key": action2.id,
                "cc": [],
                "bcc": [],
            }
        )
        result = _approve(session, action2.id, fake)
        assert result.status == "executed"
        assert result.result is not None
        assert result.result.get("deduplicated") is True


@pytest.mark.integration
def test_real_local_mcp_pulse_doc_append(tmp_path: Path, db_url: str) -> None:
    """Exit criterion: approve pulse doc_append → one entry in samples file."""
    dist = Path("mcp-server/dist/index.js")
    if not dist.exists():
        pytest.skip("mcp-server not built")

    from app.config import Settings
    from app.gate.mcp_client import McpClient

    samples = tmp_path / "samples"
    samples.mkdir()
    doc_path = samples / "appended_doc_entry.md"
    email_path = samples / "email_draft.md"
    settings = Settings(
        mcp_backend="local",
        local_doc_path=str(doc_path),
        local_email_path=str(email_path),
        mcp_server_cmd="node mcp-server/dist/index.js",
    )
    client = McpClient(settings)
    try:
        tools = handshake(client)
        assert tools == ["gmail_draft", "google_docs_append"]

        with session_scope(db_url) as session:
            pulse = propose(
                session,
                kind="doc_append",
                subject_type="pulse",
                subject_id="p_live",
                target={"document_id": "local", "heading": "## Weekly pulse"},
                payload="## Weekly pulse\n\nIntegration pulse body.",
            )
            email = propose(
                session,
                kind="email_draft",
                subject_type="pulse",
                subject_id="p_live",
                target={"to": [], "subject": "Weekly product pulse"},
                payload="Integration pulse body.",
            )
            pulse_id, email_id = pulse.id, email.id

        with session_scope(db_url) as session:
            approve(
                session,
                pulse_id,
                "tester",
                execute_fn=lambda s, i: execute(s, i, mcp=client),
            )

        assert doc_path.exists()
        text = doc_path.read_text(encoding="utf-8")
        assert "<!-- action:" in text
        assert "Integration pulse body." in text
        assert text.count("Integration pulse body.") == 1
        assert not email_path.exists()

        with session_scope(db_url) as session:
            pulse_row = session.get(models.ProposedAction, pulse_id)
            email_row = session.get(models.ProposedAction, email_id)
            assert pulse_row is not None and pulse_row.status == "executed"
            assert email_row is not None and email_row.status == "pending"
    finally:
        client.stop()
        reset_mcp_client()
