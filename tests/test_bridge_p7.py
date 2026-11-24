"""P7: cross-loop bridge — repeat fingerprints + canonical reuse."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.bridge.canonical import lookup, promote, reuse_log
from app.bridge.repeats import (
    fingerprint,
    normalize,
    record_ticket_fingerprint,
    repeat_count,
    repeat_questions,
    simhash_bucket,
)
from app.config import clear_settings_cache
from app.corpus.loader import load_corpus
from app.db import models
from app.db.session import init_db, reset_engine, session_scope
from app.gate.actions import approve
from app.gate.executor import execute
from app.llm import client as llm_mod
from app.llm.client import CuesOnlyBackend, LLMClient, reset_call_counts, reset_llm_client
from app.response.faults import clear_eval_now, reset_uncited_tracking, set_eval_now
from app.response.graph import compile_graph, reset_graph
from app.response.ingress import create_ticket
from app.response.templates import CANONICAL_LEAD
from app.weekly.pipeline import run_weekly
from sqlalchemy import select

IST = ZoneInfo("Asia/Kolkata")
SEED = Path(__file__).resolve().parents[1] / "data" / "seed_questions.json"

EXIT_LOAD_TEXTS = [
    q["text"]
    for q in json.loads(SEED.read_text(encoding="utf-8"))["questions"]
    if "exit_load_repeat" in q.get("tags", [])
]

DISTINCT_FEE_QUESTIONS = [
    "What is the expense ratio of HDFC Large Cap?",
    "Why is brokerage so high on Groww?",
    "Stamp duty on my mutual fund purchase?",
    "Hidden charges deducted from my redemption proceeds",
    "How much GST on brokerage for equity delivery?",
    "DP charges on selling stocks from demat",
    "Why was commission deducted from my SIP?",
    "Is there a platform fee on Groww mutual funds?",
    "Account maintenance charges this month?",
    "Exit load on HDFC ELSS Tax Saver — is it Nil?",
]


class FakeMcp:
    def google_docs_append(self, **kwargs):  # noqa: ANN003
        return {"documentId": kwargs.get("document_id", "local"), "deduplicated": False}

    def gmail_draft(self, **kwargs):  # noqa: ANN003
        return {"draftId": "d1", "deduplicated": False}


@pytest.fixture()
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("EVAL_FAULTS", "")
    monkeypatch.setenv("LLM_PROVIDER", "cues_only")
    clear_settings_cache()
    reset_engine()
    reset_graph()
    reset_llm_client()
    reset_call_counts()
    reset_uncited_tracking()
    clear_eval_now()
    llm_mod._CLIENT = LLMClient(backend=CuesOnlyBackend())
    load_corpus()
    url = f"sqlite:///{tmp_path / 'p7.sqlite3'}"
    init_db(url)
    yield url
    clear_eval_now()
    reset_uncited_tracking()
    reset_engine()
    reset_graph()
    reset_llm_client()
    clear_settings_cache()


@pytest.fixture()
def compiled(tmp_path: Path):
    return compile_graph(path=tmp_path / "checkpoints.sqlite3")


def _current_draft(session, ticket_id: str) -> models.DraftVersion:
    return session.scalars(
        select(models.DraftVersion).where(
            models.DraftVersion.ticket_id == ticket_id,
            models.DraftVersion.is_current.is_(True),
        )
    ).one()


def _explainer_actions(session) -> tuple[models.ProposedAction, models.ProposedAction]:
    doc = session.scalars(
        select(models.ProposedAction).where(
            models.ProposedAction.subject_type == "fee_explainer",
            models.ProposedAction.kind == "doc_append",
            models.ProposedAction.status == "pending",
        )
    ).one()
    email = session.scalars(
        select(models.ProposedAction).where(
            models.ProposedAction.subject_type == "fee_explainer",
            models.ProposedAction.kind == "email_draft",
            models.ProposedAction.status == "pending",
        )
    ).one()
    return doc, email


# --- P7.5 fingerprint tuning --------------------------------------------------------


def test_exit_load_rephrasings_collide() -> None:
    assert len(EXIT_LOAD_TEXTS) == 6
    fps = {fingerprint("fees_brokerage", "Exit load", "factual", text) for text in EXIT_LOAD_TEXTS}
    assert len(fps) == 1
    buckets = {simhash_bucket(normalize(text)) for text in EXIT_LOAD_TEXTS}
    assert len(buckets) == 1


def test_distinct_fee_questions_do_not_collide() -> None:
    exit_fp = fingerprint("fees_brokerage", "Exit load", "factual", EXIT_LOAD_TEXTS[0])
    distinct_fps: set[str] = set()
    for text in DISTINCT_FEE_QUESTIONS:
        section = (
            "Exit load"
            if "exit" in text.lower()
            else ("Key fund facts" if "expense" in text.lower() else "uncovered")
        )
        fp = fingerprint("fees_brokerage", section, "factual", text)
        distinct_fps.add(fp)
        assert fp != exit_fp
    assert len(distinct_fps) == len(DISTINCT_FEE_QUESTIONS)


# --- P7.1 / P7.2 repeats → pulse ----------------------------------------------------


def test_six_exit_load_tickets_appear_in_pulse(db_url: str, compiled) -> None:
    """Seed 6 exit-load rephrasings, then weekly: pulse lists count=6 on ticket clock."""
    now = datetime(2026, 11, 25, 12, 0, tzinfo=IST)
    set_eval_now(now)
    with session_scope(db_url) as session:
        for text in EXIT_LOAD_TEXTS:
            create_ticket(text, session=session, compiled=compiled)
        tickets = list(session.scalars(select(models.Ticket)).all())
        assert len(tickets) == 6
        fps = {t.fingerprint for t in tickets}
        assert len(fps) == 1
        assert None not in fps

    result = run_weekly(
        as_of=datetime(2026, 7, 4, tzinfo=IST).date(),
        trigger="manual",
        db_url=db_url,
        now=now,
        action_ideas_fn=lambda *_a, **_k: [
            "Clarify redemption messaging.",
            "Surface fee breakdown.",
            "Link scheme page on redeem.",
        ],
    )
    assert result.status == "awaiting_approval"
    assert result.noop is False

    with session_scope(db_url) as session:
        pulse = session.scalars(select(models.Pulse)).one()
        repeats = list(pulse.repeat_questions or [])
        assert repeats, "expected repeat-questions section to be populated"
        top = repeats[0]
        assert int(top["count"]) == 6
        assert top["window_start"]
        assert top["window_end"]
        # Ticket clock (G1): window ends on ticket-now, not review as_of 2026-07-04.
        assert top["window_end"] == now.astimezone(IST).date().isoformat()
        assert "2026-07-04" not in str(top.get("window") or "")
        assert "exit" in top["representative_text"].lower()
        assert top.get("provenance") == "question"
        assert "Repeat questions from support" in pulse.body_md
        assert "6×" in pulse.body_md


def test_fewer_than_five_repeats_omitted_and_seven_day_boundary(db_url: str, compiled) -> None:
    now = datetime(2026, 11, 25, 12, 0, tzinfo=IST)
    text = EXIT_LOAD_TEXTS[0]

    # One ticket exactly 7 days old → excluded from (now−7d, now].
    set_eval_now(now - timedelta(days=7))
    with session_scope(db_url) as session:
        create_ticket(text, session=session, compiled=compiled)

    # Four recent tickets → cluster of 4 (< min_count 5).
    set_eval_now(now - timedelta(hours=1))
    with session_scope(db_url) as session:
        for i in range(4):
            create_ticket(
                EXIT_LOAD_TEXTS[i + 1],
                session=session,
                compiled=compiled,
                ticket_id=f"tkt_recent_{i}",
            )

    set_eval_now(now)
    with session_scope(db_url) as session:
        repeats = repeat_questions(now, min_count=5, session=session)
        assert repeats == []
        fp = fingerprint("fees_brokerage", "Exit load", "factual", text)
        assert repeat_count(session, fp, now) == 4


def test_burst_flag_on_identical_normalized_texts(db_url: str) -> None:
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    text = "Why was I charged an exit load on HDFC Large Cap Fund?"
    with session_scope(db_url) as session:
        for i in range(5):
            ticket = models.Ticket(
                id=f"tkt_burst_{i}",
                received_at=now - timedelta(minutes=i),
                channel="test",
                question_redacted=text,
                pii_types_found=[],
                fingerprint=None,
                status="awaiting_human",
                rework_count=0,
                refinement_count=0,
            )
            session.add(ticket)
            session.flush()
            record_ticket_fingerprint(
                session,
                ticket,
                theme_id="fees_brokerage",
                section="Exit load",
                compliance="factual",
                now=now,
            )
        repeats = repeat_questions(now, min_count=5, session=session)
        assert len(repeats) == 1
        assert repeats[0]["possible_duplicate_burst"] is True


def test_rejected_tickets_still_count(db_url: str) -> None:
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    text = EXIT_LOAD_TEXTS[0]
    with session_scope(db_url) as session:
        for i in range(5):
            ticket = models.Ticket(
                id=f"tkt_rej_{i}",
                received_at=now - timedelta(hours=i),
                channel="test",
                question_redacted=text,
                pii_types_found=[],
                fingerprint=None,
                status="rejected" if i < 2 else "awaiting_human",
                rework_count=0,
                refinement_count=0,
            )
            session.add(ticket)
            session.flush()
            record_ticket_fingerprint(
                session,
                ticket,
                theme_id="fees_brokerage",
                section="Exit load",
                compliance="factual",
                now=now,
            )
        repeats = repeat_questions(now, min_count=5, session=session)
        assert repeats[0]["count"] == 5


# --- P7.3 / P7.4 canonical reuse ----------------------------------------------------


def test_canonical_reuse_after_explainer_doc_append(db_url: str, compiled) -> None:
    now = datetime(2026, 11, 25, 12, 0, tzinfo=IST)
    set_eval_now(now)
    fake = FakeMcp()

    result = run_weekly(
        as_of=datetime(2026, 7, 4, tzinfo=IST).date(),
        trigger="manual",
        db_url=db_url,
        now=now,
        action_ideas_fn=lambda *_a, **_k: [
            "Clarify redemption messaging.",
            "Surface fee breakdown.",
            "Link scheme page on redeem.",
        ],
    )
    assert result.status == "awaiting_approval"

    fee_q = "What is the exit load on HDFC Large Cap?"

    # (a) Before any approval → row 4.
    with session_scope(db_url) as session:
        ticket_a = create_ticket(fee_q, session=session, compiled=compiled)
        draft_a = _current_draft(session, ticket_a.id)
        assert draft_a.origin != "canonical"
        assert (draft_a.routing_note or {}).get("why") or draft_a.reply_text
        # Decision table row is stored on the draft via origin / content.
        assert CANONICAL_LEAD not in draft_a.reply_text

    with session_scope(db_url) as session:
        doc, email = _explainer_actions(session)
        doc_id, email_id = doc.id, email.id

    # (b) Approve email_draft only → still no promotion.
    with session_scope(db_url) as session:
        approve(
            session,
            email_id,
            "tester",
            execute_fn=lambda s, i: execute(s, i, mcp=fake),
        )
        assert lookup("fees_brokerage", "Exit load", "large_cap", session=session) is None

    # (c) Resubmit → still row 4.
    with session_scope(db_url) as session:
        ticket_c = create_ticket(
            fee_q,
            session=session,
            compiled=compiled,
            ticket_id="tkt_after_email",
        )
        draft_c = _current_draft(session, ticket_c.id)
        assert CANONICAL_LEAD not in draft_c.reply_text
        assert draft_c.origin != "canonical"

    # (d) Approve doc_append → promote.
    with session_scope(db_url) as session:
        approve(
            session,
            doc_id,
            "tester",
            execute_fn=lambda s, i: execute(s, i, mcp=fake),
        )
        action = session.get(models.ProposedAction, doc_id)
        assert action is not None
        assert action.result is not None
        assert action.result.get("promotion") == "promoted"
        hit = lookup("fees_brokerage", "Exit load", "large_cap", session=session)
        assert hit is not None
        assert hit.get("bullets")

    # (e) Resubmit → row 3 + reuse log entry.
    with session_scope(db_url) as session:
        ticket_e = create_ticket(
            fee_q,
            session=session,
            compiled=compiled,
            ticket_id="tkt_after_doc",
        )
        draft_e = _current_draft(session, ticket_e.id)
        assert draft_e.origin == "canonical"
        assert CANONICAL_LEAD in draft_e.reply_text
        assert draft_e.citation_url
        log = reuse_log(session)
        assert len(log) == 1
        assert log[0]["ticket_id"] == ticket_e.id

        # ELSS exit load stays row 4 (Nil; not in covers family).
        ticket_elss = create_ticket(
            "What is the exit load on HDFC ELSS Tax Saver?",
            session=session,
            compiled=compiled,
            ticket_id="tkt_elss",
        )
        draft_elss = _current_draft(session, ticket_elss.id)
        assert draft_elss.origin != "canonical"
        assert "Nil" in draft_elss.reply_text or "nil" in draft_elss.reply_text.lower()


def test_promote_skips_older_than_active(db_url: str) -> None:
    with session_scope(db_url) as session:
        older = models.WeeklyRun(
            id="wr_old",
            as_of=datetime(2026, 6, 1, tzinfo=IST).date(),
            trigger="manual",
            status="awaiting_approval",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            input_hash="h_old",
        )
        newer = models.WeeklyRun(
            id="wr_new",
            as_of=datetime(2026, 7, 4, tzinfo=IST).date(),
            trigger="manual",
            status="awaiting_approval",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            input_hash="h_new",
        )
        session.add_all([older, newer])
        block = load_corpus().get("large_cap", "Exit load")
        assert block is not None
        fe_new = models.FeeExplainer(
            id="fe_new",
            run_id="wr_new",
            bullets=[
                {
                    "text": block.text.split(".")[0].strip() + ".",
                    "source_url": block.source_url,
                    "scheme_id": "large_cap",
                    "section": "Exit load",
                    "last_checked": block.last_checked,
                    "snapshot": block.snapshot,
                }
            ],
            source_urls=[block.source_url],
            last_checked=block.last_checked,
            status="ready",
        )
        fe_old = models.FeeExplainer(
            id="fe_old",
            run_id="wr_old",
            bullets=list(fe_new.bullets),
            source_urls=list(fe_new.source_urls),
            last_checked="01-Sep-2026",
            status="ready",
        )
        session.add_all([fe_new, fe_old])
        session.flush()

        first = promote(session, "fe_new", "act_new")
        assert first.status == "promoted"
        second = promote(session, "fe_old", "act_old")
        assert second.status == "skipped"
        assert "older than active" in second.detail


def test_pattern_in_week_repeats_uses_real_counts(db_url: str, compiled) -> None:
    now = datetime(2026, 11, 25, 12, 0, tzinfo=IST)
    set_eval_now(now)
    with session_scope(db_url) as session:
        for i, text in enumerate(EXIT_LOAD_TEXTS):
            create_ticket(
                text,
                session=session,
                compiled=compiled,
                ticket_id=f"tkt_pat_{i}",
            )
        last = session.get(models.Ticket, "tkt_pat_5")
        assert last is not None
        assert last.priority == "P1"
        draft = _current_draft(session, last.id)
        evidence = (draft.routing_note or {}).get("evidence") or {}
        assert int(evidence.get("in_week_repeats") or 0) >= 5
