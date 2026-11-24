"""Startup checks and gate read-only mode (arch §16, plan P11.4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.db.session import session_scope
from app.gate import executor
from app.gate.actions import GateError, approve, propose
from app.gate.mode import gate_mode, set_gate_mode
from app.startup import StartupCheckError, mcp_handshake, pii_self_test


@pytest.fixture(autouse=True)
def _reset_mode():
    set_gate_mode("full")
    yield
    set_gate_mode("full")


def test_pii_self_test_passes() -> None:
    pii_self_test()


def test_pii_self_test_fails_when_redaction_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.startup as startup

    class _Broken:
        def __init__(self, text: str) -> None:
            self.text = text
            self.types: set[str] = set()

    monkeypatch.setattr(startup, "redact", lambda text: _Broken(text))
    with pytest.raises(StartupCheckError):
        pii_self_test()


class _FakeClient:
    def __init__(self, tools: list[str] | None = None, error: Exception | None = None) -> None:
        self.tools = tools or []
        self.error = error

    def list_tools(self) -> list[str]:
        if self.error:
            raise self.error
        return self.tools


@pytest.mark.parametrize(
    ("client", "want"),
    [
        (_FakeClient(["google_docs_append", "gmail_draft"]), "full"),
        (_FakeClient(error=FileNotFoundError("node")), "read_only"),
        (_FakeClient(["google_docs_append", "gmail_draft", "gmail_send"]), "read_only"),
    ],
)
def test_mcp_handshake_sets_gate_mode(monkeypatch: pytest.MonkeyPatch, client, want) -> None:
    monkeypatch.setattr(executor, "get_mcp_client", lambda *a, **k: client)
    assert mcp_handshake() is (want == "full")
    assert gate_mode() == want


def test_read_only_gate_pauses_doc_and_email_not_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import clear_settings_cache
    from app.db.session import init_db, reset_engine

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'g.sqlite3'}")
    clear_settings_cache()
    reset_engine()
    init_db()
    executed: list[str] = []

    def fake_execute(session, action_id):
        executed.append(action_id)
        return session.get(__import__("app.db.models", fromlist=["x"]).ProposedAction, action_id)

    set_gate_mode("read_only", "test")
    with session_scope() as session:
        doc = propose(
            session,
            kind="doc_append",
            subject_type="pulse",
            subject_id="p_1",
            target={"document_id": "local"},
            payload="## Pulse",
        )
        reply = propose(
            session,
            kind="reply_send",
            subject_type="ticket",
            subject_id="t_1",
            target={"ticket_id": "t_1"},
            payload="Exit load of 1% if redeemed within 1 year.",
        )
        doc_id, reply_id = doc.id, reply.id
    with session_scope() as session:
        with pytest.raises(GateError) as exc:
            approve(session, doc_id, "tester", execute_fn=fake_execute)
        assert exc.value.code == "GATE_READ_ONLY"
        approve(session, reply_id, "tester", execute_fn=fake_execute)
    assert executed == [reply_id]
    reset_engine()
    clear_settings_cache()
