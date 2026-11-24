"""DB models, PII-safe writes, snapshot/diff (P2.7–P2.8)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.db import models
from app.db.repository import PIILeakError, add
from app.db.session import init_db, reset_engine, session_scope
from app.db.snapshots import diff, snapshot


@pytest.fixture()
def db_url(tmp_path):  # type: ignore[no-untyped-def]
    reset_engine()
    url = f"sqlite:///{tmp_path / 'test.sqlite3'}"
    init_db(url)
    yield url
    reset_engine()


def test_planted_pan_raises_pii_leak(db_url: str) -> None:
    with session_scope(db_url) as session:
        ticket = models.Ticket(
            id="t_1",
            received_at=datetime.now(UTC),
            channel="web",
            question_redacted="My PAN is ABCDE1234F",
            pii_types_found=[],
            status="processing",
            rework_count=0,
            refinement_count=0,
        )
        with pytest.raises(PIILeakError) as exc:
            add(session, ticket)
        assert "PAN" in exc.value.types


def test_redacted_ticket_persists(db_url: str) -> None:
    with session_scope(db_url) as session:
        ticket = models.Ticket(
            id="t_2",
            received_at=datetime.now(UTC),
            channel="web",
            question_redacted="What is the exit load for [PAN]?",
            pii_types_found=["PAN"],
            status="processing",
            rework_count=0,
            refinement_count=0,
        )
        add(session, ticket)
    with session_scope(db_url) as session:
        row = session.get(models.Ticket, "t_2")
        assert row is not None
        assert "[PAN]" in row.question_redacted


def test_snapshot_diff_round_trip(db_url: str) -> None:
    with session_scope(db_url) as session:
        add(
            session,
            models.Ticket(
                id="t_3",
                received_at=datetime.now(UTC),
                channel="web",
                question_redacted="clean question",
                pii_types_found=[],
                status="processing",
                rework_count=0,
                refinement_count=0,
            ),
        )
        before = snapshot(session)

    with session_scope(db_url) as session:
        ticket = session.get(models.Ticket, "t_3")
        assert ticket is not None
        ticket.status = "awaiting_human"
        session.flush()
        after = snapshot(session)
        delta = diff(before, after)
        assert not delta.is_empty()
        assert "tickets" in delta.changed
        # Round-trip: identical snapshots → empty diff
        assert diff(after, after).is_empty()
