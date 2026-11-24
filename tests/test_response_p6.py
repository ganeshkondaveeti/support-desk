"""P6: orchestrator decision table, reviewer, rework, refinement, human actions."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import clear_settings_cache
from app.corpus.loader import load_corpus
from app.db import models
from app.db.session import init_db, reset_engine, session_scope
from app.db.snapshots import diff, snapshot
from app.gate.actions import approve
from app.llm import client as llm_mod
from app.llm.client import CuesOnlyBackend, LLMClient, reset_call_counts, reset_llm_client
from app.response.faults import (
    clear_ticket_fault,
    reset_uncited_tracking,
    set_ticket_fault,
)
from app.response.graph import compile_graph, reset_graph
from app.response.human import edit_ticket, reject_ticket
from app.response.ingress import create_ticket
from app.response.orchestrator import compose_draft
from app.response.refinement import RefinementError, refine_ticket
from app.response.reviewer import review_draft
from app.response.templates import ADVICE_REFUSAL, PERFORMANCE_REFUSAL
from sqlalchemy import select

PLANTED_PAN = "ABCDE1234F"
PLANTED_PHONE = "9876543210"


@pytest.fixture()
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # APP_ENV=test allows X-Eval-Ticket; leave EVAL_FAULTS empty so ordinary
    # tickets are not fault-injected (GC-11 applies faults per ticket).
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("EVAL_FAULTS", "")
    clear_settings_cache()
    reset_engine()
    reset_graph()
    reset_llm_client()
    reset_call_counts()
    reset_uncited_tracking()
    clear_ticket_fault()
    llm_mod._CLIENT = LLMClient(backend=CuesOnlyBackend())
    load_corpus()
    url = f"sqlite:///{tmp_path / 'p6.sqlite3'}"
    init_db(url)
    yield url
    clear_ticket_fault()
    reset_uncited_tracking()
    reset_engine()
    reset_graph()
    reset_llm_client()
    clear_settings_cache()


@pytest.fixture()
def compiled(tmp_path: Path):
    return compile_graph(path=tmp_path / "checkpoints.sqlite3")


def _current_draft(session, ticket_id: str) -> models.DraftVersion:
    row = session.scalars(
        select(models.DraftVersion).where(
            models.DraftVersion.ticket_id == ticket_id,
            models.DraftVersion.is_current.is_(True),
        )
    ).one()
    return row


def _reply_sends(session, ticket_id: str) -> list[models.ProposedAction]:
    return list(
        session.scalars(
            select(models.ProposedAction).where(
                models.ProposedAction.subject_type == "ticket",
                models.ProposedAction.subject_id == ticket_id,
                models.ProposedAction.kind == "reply_send",
            )
        ).all()
    )


# --- Decision table -----------------------------------------------------------------


def test_row1_advice_refusal(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "Should I redeem my HDFC Large Cap units?",
            session=session,
            compiled=compiled,
        )
        draft = _current_draft(session, ticket.id)
    assert ticket.status == "awaiting_human"
    assert ADVICE_REFUSAL in draft.reply_text
    assert draft.reviewer_result.get("status") == "pass"
    # Row is stored on graph draft; routing should mention Compliance.
    assert ticket.route_team and "Compliance" in ticket.route_team


def test_row2_performance_refusal(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "What returns / CAGR can I expect from HDFC Mid Cap?",
            session=session,
            compiled=compiled,
        )
        draft = _current_draft(session, ticket.id)
    assert PERFORMANCE_REFUSAL in draft.reply_text
    assert draft.citation_url


def test_row4_covered_fact(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "What is the expense ratio of HDFC Large Cap?",
            session=session,
            compiled=compiled,
        )
        draft = _current_draft(session, ticket.id)
    assert "Expense ratio" in draft.reply_text or "1.02" in draft.reply_text
    assert draft.citation_url
    assert draft.last_checked
    assert ticket.status == "awaiting_human"
    assert len(_reply_sends(session, ticket.id)) >= 1 or True  # session closed
    with session_scope(db_url) as session:
        assert any(a.status == "pending" for a in _reply_sends(session, ticket.id))


def test_row5_not_covered(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "What is the KYC status of my account?",
            session=session,
            compiled=compiled,
        )
        draft = _current_draft(session, ticket.id)
    assert "isn't covered" in draft.reply_text
    assert draft.citation_url is None
    # The template is reviewer-clean: not blocked, and a reply is proposed.
    assert draft.reviewer_result["status"] == "pass", draft.reviewer_result
    assert ticket.status == "awaiting_human"


def test_row3_without_canonical_falls_to_row4(db_url: str, compiled) -> None:
    """Without an approved canonical explainer, fee facts use row 4."""
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "What is the exit load on HDFC Large Cap?",
            session=session,
            compiled=compiled,
        )
        draft = _current_draft(session, ticket.id)
    assert "Exit load" in draft.reply_text or "exit load" in draft.reply_text.lower()
    assert draft.origin in {"orchestrator", "rework"}
    assert draft.origin != "canonical"


def test_arch_88_walkthrough(db_url: str, compiled) -> None:
    """PAN + phone + should I redeem → redacted, advice row, cited fact, routing."""
    text = (
        f"Why was I charged an exit load? My PAN is {PLANTED_PAN}, "
        f"call me on {PLANTED_PHONE}. Should I just redeem everything?"
    )
    with session_scope(db_url) as session:
        ticket = create_ticket(text, session=session, compiled=compiled)
        draft = _current_draft(session, ticket.id)
    assert PLANTED_PAN not in ticket.question_redacted
    assert PLANTED_PHONE not in ticket.question_redacted
    assert "[PAN]" in ticket.question_redacted
    assert "[PHONE]" in ticket.question_redacted
    assert ADVICE_REFUSAL in draft.reply_text
    assert draft.citation_url
    assert ticket.priority in {"P1", "P2"}
    assert ticket.route_team and "Compliance" in ticket.route_team
    assert len(draft.reply_text.split(".")) <= 6  # ≤3 sentences roughly


# --- Reviewer -----------------------------------------------------------------------


def test_reviewer_hard_fails() -> None:
    load_corpus()
    assert (
        review_draft({"reply_text": f"Contact me at {PLANTED_PAN}.", "row": 5})["failures"]
        == ["PII"]
        or "PII"
        in review_draft({"reply_text": f"Contact me at {PLANTED_PAN}.", "row": 5})["failures"]
    )

    long = "One. Two. Three. Four."
    assert "LENGTH" in review_draft({"reply_text": long, "row": 5})["failures"]

    assert (
        "ADVICE"
        in review_draft({"reply_text": "You may want to consider staying invested.", "row": 5})[
            "failures"
        ]
    )

    assert (
        "PERFORMANCE"
        in review_draft({"reply_text": "The fund returned 18% last year.", "row": 5})["failures"]
    )

    assert (
        "URL_SCOPE"
        in review_draft(
            {
                "reply_text": "See https://evil.example.com/page for details.",
                "row": 5,
            }
        )["failures"]
    )

    assert (
        "UNCITED_CLAIM"
        in review_draft(
            {
                "reply_text": "Exit load of 9% applies after 10 years.",
                "citation_url": None,
                "row": 4,
            }
        )["failures"]
    )


def test_reviewer_corpus_percent_passes() -> None:
    load_corpus()
    corpus = load_corpus()
    block = corpus.get("large_cap", "Stamp duty and tax")
    assert block is not None
    # Pick a corpus sentence that may contain % (tax) — must pass with citation.
    sentence = block.text.split(". ")[0].strip()
    if not sentence.endswith("."):
        sentence += "."
    result = review_draft(
        {
            "reply_text": f"Per the scheme page: {sentence}",
            "citation_url": block.source_url,
            "last_checked": block.last_checked,
            "row": 4,
        },
        facts={
            "answer_sentences": [sentence],
            "citation_url": block.source_url,
            "covered": True,
        },
    )
    assert result["status"] == "pass", result


def test_reviewer_generated_return_fails() -> None:
    load_corpus()
    result = review_draft(
        {
            "reply_text": "Per the scheme page: The fund returned 18% last year.",
            "citation_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            "last_checked": "01-Sep-2026",
            "row": 4,
        },
        facts={"answer_sentences": [], "citation_url": None, "covered": False},
    )
    assert result["status"] == "fail"
    assert set(result["failures"]) & {"PERFORMANCE", "UNCITED_CLAIM", "ADVICE"}


# --- Rework / faults ----------------------------------------------------------------


def test_uncited_once_reworks_then_passes(db_url: str, compiled) -> None:
    set_ticket_fault("uncited_once")
    try:
        with session_scope(db_url) as session:
            ticket = create_ticket(
                "What is the expense ratio of HDFC Large Cap?",
                session=session,
                compiled=compiled,
                ticket_id="t_uncited",
            )
            draft = _current_draft(session, ticket.id)
            actions = _reply_sends(session, ticket.id)
        assert ticket.status == "awaiting_human"
        assert ticket.rework_count == 1
        assert draft.reviewer_result.get("status") == "pass"
        assert any(a.status == "pending" for a in actions)
        assert "returned 18%" not in draft.reply_text
    finally:
        clear_ticket_fault()


def test_advice_always_ends_blocked_no_reply_send(db_url: str, compiled) -> None:
    set_ticket_fault("advice_always")
    try:
        with session_scope(db_url) as session:
            ticket = create_ticket(
                "What is the expense ratio of HDFC Large Cap?",
                session=session,
                compiled=compiled,
                ticket_id="t_advice",
            )
            actions = _reply_sends(session, ticket.id)
            draft = _current_draft(session, ticket.id)
        assert ticket.status == "blocked"
        assert ticket.rework_count == 1
        assert not any(a.status == "pending" for a in actions)
        assert draft.reviewer_result.get("status") == "blocked"
        assert "ADVICE" in (draft.reviewer_result.get("flags") or [])
    finally:
        clear_ticket_fault()


# --- Refinement ---------------------------------------------------------------------


def test_refine_different_scheme_reruns_facts_only(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "What is the expense ratio of HDFC Large Cap?",
            session=session,
            compiled=compiled,
        )
        before = session.scalars(
            select(models.AgentOutput).where(models.AgentOutput.ticket_id == ticket.id)
        ).all()
        before_counts = {a.agent: sum(1 for x in before if x.agent == a.agent) for a in before}
        refine_ticket(
            session,
            ticket.id,
            "different_scheme",
            "Please answer for HDFC Mid Cap instead",
            compiled=compiled,
        )
        after = session.scalars(
            select(models.AgentOutput).where(models.AgentOutput.ticket_id == ticket.id)
        ).all()
    facts_after = sum(1 for a in after if a.agent == "facts")
    pattern_after = sum(1 for a in after if a.agent == "pattern")
    compliance_after = sum(1 for a in after if a.agent == "compliance")
    assert facts_after == before_counts.get("facts", 1) + 1
    assert pattern_after == before_counts.get("pattern", 1)
    assert compliance_after == before_counts.get("compliance", 1)


def test_refine_shorter_adds_no_agent_outputs(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "What is the expense ratio of HDFC Large Cap?",
            session=session,
            compiled=compiled,
        )
        before = len(
            session.scalars(
                select(models.AgentOutput).where(models.AgentOutput.ticket_id == ticket.id)
            ).all()
        )
        refine_ticket(session, ticket.id, "shorter", compiled=compiled)
        after = len(
            session.scalars(
                select(models.AgentOutput).where(models.AgentOutput.ticket_id == ticket.id)
            ).all()
        )
        draft = _current_draft(session, ticket.id)
    assert after == before
    assert ticket.refinement_count == 1
    assert draft.origin == "refinement"


def test_sixth_refinement_returns_409(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "What is the expense ratio of HDFC Large Cap?",
            session=session,
            compiled=compiled,
        )
        for _ in range(5):
            refine_ticket(session, ticket.id, "shorter", compiled=compiled)
        with pytest.raises(RefinementError) as exc:
            refine_ticket(session, ticket.id, "shorter", compiled=compiled)
        assert exc.value.status_code == 409
        assert exc.value.code == "refinement_cap_reached"


# --- Edit / reject ------------------------------------------------------------------


def test_reject_only_this_ticket(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        t1 = create_ticket(
            "What is the expense ratio of HDFC Large Cap?",
            session=session,
            compiled=compiled,
            ticket_id="t_rej1",
        )
        t2 = create_ticket(
            "What is the exit load on HDFC Mid Cap?",
            session=session,
            compiled=compiled,
            ticket_id="t_rej2",
        )
        before = snapshot(session)
        reject_ticket(session, t1.id, "tester")
        after = snapshot(session)
        d = diff(before, after)
        assert session.get(models.Ticket, t1.id).status == "rejected"
        assert session.get(models.Ticket, t2.id).status == "awaiting_human"
        a1 = _reply_sends(session, t1.id)
        assert any(a.status == "rejected" for a in a1)
        a2 = _reply_sends(session, t2.id)
        assert any(a.status == "pending" for a in a2)
        # t2 ticket row unchanged.
        t2_changes = [c for c in d.changed.get("tickets", []) if c.get("key") == "t_rej2"]
        assert t2_changes == []


def test_edit_pass_reproposes_reply_send(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "What is the expense ratio of HDFC Large Cap?",
            session=session,
            compiled=compiled,
        )
        current = _current_draft(session, ticket.id)
        # Identical edit is a no-op.
        noop = edit_ticket(session, ticket.id, current.reply_text, "tester")
        assert noop.id == current.id

        corpus = load_corpus()
        block = corpus.get("large_cap", "Key fund facts")
        assert block is not None
        sentence = next(s for s in block.text.split(". ") if "Expense ratio" in s)
        if not sentence.endswith("."):
            sentence += "."
        # Alternate lead-in so the edit differs from the orchestrator draft.
        text = f"From the scheme information: {sentence}"
        assert text != current.reply_text
        before = snapshot(session)
        edited = edit_ticket(session, ticket.id, text, "tester")
        after = snapshot(session)
        d = diff(before, after)
        assert edited.origin == "human_edit"
        assert ticket.status == "awaiting_human", edited.reviewer_result
        pending = [a for a in _reply_sends(session, ticket.id) if a.status == "pending"]
        assert len(pending) == 1
        assert pending[0].payload == text
        assert any(row.get("origin") == "human_edit" for row in d.added.get("draft_versions", []))


def test_edit_fail_blocks_and_supersedes(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "What is the expense ratio of HDFC Large Cap?",
            session=session,
            compiled=compiled,
        )
        edit_ticket(
            session,
            ticket.id,
            "You should redeem now. The fund returned 18% last year.",
            "tester",
        )
        assert ticket.status == "blocked"
        pending = [a for a in _reply_sends(session, ticket.id) if a.status == "pending"]
        assert pending == []


def test_blocked_then_passing_edit_creates_reply_send(db_url: str, compiled) -> None:
    set_ticket_fault("advice_always")
    try:
        with session_scope(db_url) as session:
            ticket = create_ticket(
                "What is the expense ratio of HDFC Large Cap?",
                session=session,
                compiled=compiled,
                ticket_id="t_g9",
            )
            assert ticket.status == "blocked"
            assert not any(a.status == "pending" for a in _reply_sends(session, ticket.id))
    finally:
        clear_ticket_fault()

    with session_scope(db_url) as session:
        corpus = load_corpus()
        block = corpus.get("large_cap", "Key fund facts")
        assert block is not None
        sentence = [s for s in block.text.split(". ") if "Expense ratio" in s][0]
        if not sentence.endswith("."):
            sentence += "."
        text = f"Per the scheme page: {sentence}"
        edit_ticket(session, "t_g9", text, "tester")
        ticket = session.get(models.Ticket, "t_g9")
        assert ticket is not None
        assert ticket.status == "awaiting_human"
        pending = [a for a in _reply_sends(session, "t_g9") if a.status == "pending"]
        assert len(pending) == 1


def test_approve_writes_outbox(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "What is the expense ratio of HDFC Large Cap?",
            session=session,
            compiled=compiled,
        )
        action = next(a for a in _reply_sends(session, ticket.id) if a.status == "pending")
        approve(session, action.id, "tester")
        outbox = session.scalars(
            select(models.OutboxReply).where(models.OutboxReply.ticket_id == ticket.id)
        ).all()
        assert len(outbox) == 1
        assert outbox[0].reply_text == action.payload


def test_fault_injection_refused_outside_test(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    import logging

    from app.response.faults import check_fault_injection_on_startup, fault_applies

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("EVAL_FAULTS", "advice_always")
    clear_settings_cache()
    with caplog.at_level(logging.WARNING):
        check_fault_injection_on_startup()
    assert any("fault injection refused" in r.message for r in caplog.records)
    assert fault_applies("advice_always") is False
    clear_settings_cache()


def test_compose_draft_unit_rows() -> None:
    load_corpus()
    facts = {
        "scheme_id": "large_cap",
        "section": "Exit load",
        "covered": True,
        "answer_sentences": ["Exit load of 1% if redeemed within 1 year."],
        "citation_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        "snapshot": "B",
        "last_checked": "01-Sep-2026",
        "searched": {},
        "multi_scheme": [],
        "family": None,
        "family_schemes": [],
    }
    pattern = {
        "theme_id": "fees_brokerage",
        "trend": "recurring",
        "review_count": 136,
        "window": ["2026-04-12", "2026-07-04"],
        "review_ids_sample": [],
        "in_week_repeats": 0,
        "repeat_window": ["2026-11-18", "2026-11-24"],
    }
    compliance = {
        "advice": False,
        "performance_claim": False,
        "pii_detected": False,
        "pii_types": [],
    }
    draft = compose_draft(
        {"facts": facts, "pattern": pattern, "compliance": compliance, "failures": []}
    )
    assert draft["row"] == 4
    assert draft["close_on_send"] is True

    compliance_adv = {**compliance, "advice": True}
    d1 = compose_draft(
        {"facts": facts, "pattern": pattern, "compliance": compliance_adv, "failures": []}
    )
    assert d1["row"] == 1
    assert ADVICE_REFUSAL in d1["reply_text"]


def test_known_pii_types_merge_and_validate() -> None:
    from app.response.ingress import merge_pii_types

    assert merge_pii_types({"PHONE"}, ["PAN"]) == ["PAN", "PHONE"]
    assert merge_pii_types(set(), []) == []
    with pytest.raises(ValueError):
        merge_pii_types(set(), ["NOT_A_TYPE"])
