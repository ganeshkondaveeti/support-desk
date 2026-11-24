"""P5 response loop: agents, graph, ingress, seed questions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from app.corpus.loader import load_corpus
from app.db import models
from app.db.session import init_db, reset_engine, session_scope
from app.llm import client as llm_mod
from app.llm.client import (
    CuesOnlyBackend,
    LLMClient,
    call_counts,
    reset_call_counts,
    reset_llm_client,
)
from app.response.agents.compliance import run_compliance
from app.response.agents.facts import detect_out_of_section, detect_schemes, run_facts
from app.response.graph import compile_graph, reset_graph
from app.response.ingress import create_ticket
from app.response.seed import load_seed_file, seed_tickets
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
PLANTED_PAN = "ABCDE1234F"


@pytest.fixture()
def db_url(tmp_path: Path):
    reset_engine()
    reset_graph()
    reset_llm_client()
    reset_call_counts()
    llm_mod._CLIENT = LLMClient(backend=CuesOnlyBackend())
    load_corpus()
    url = f"sqlite:///{tmp_path / 'p5.sqlite3'}"
    init_db(url)
    yield url
    reset_engine()
    reset_graph()
    reset_llm_client()


@pytest.fixture()
def compiled(tmp_path: Path):
    return compile_graph(path=tmp_path / "checkpoints.sqlite3")


def test_llm_call_counter_one_per_agent(db_url: str, compiled) -> None:
    reset_call_counts()
    with session_scope(db_url) as session:
        create_ticket(
            "What is the expense ratio of HDFC Large Cap?",
            session=session,
            compiled=compiled,
        )
    counts = call_counts()
    assert counts.get("facts") == 1
    assert counts.get("pattern") == 1
    assert counts.get("compliance") == 1


def test_agents_same_superstep(db_url: str, compiled, tmp_path: Path) -> None:
    """Trace confirms facts/pattern/compliance start before any of them returns."""
    app = compiled
    events: list[tuple[str, str]] = []
    payload = {
        "ticket_id": "t_super",
        "question": "Exit load on HDFC Mid Cap?",
        "pii_types": [],
        "refine_kind": None,
        "refine_hint": None,
        "rework_count": 0,
        "refinement_count": 0,
        "failures": [],
        "agent_runs": {},
    }
    config = {"configurable": {"thread_id": "t_super"}}
    for mode, event in app.stream(payload, config=config, stream_mode=["tasks", "values"]):
        if mode != "tasks":
            continue
        name = event["name"]
        if name not in {"facts", "pattern", "compliance"}:
            continue
        kind = "return" if ("result" in event or "error" in event) else "start"
        events.append((kind, name))
    starts = [n for k, n in events if k == "start"]
    returns = [n for k, n in events if k == "return"]
    assert set(starts) == {"facts", "pattern", "compliance"}
    assert set(returns) == {"facts", "pattern", "compliance"}
    # Same superstep: no agent return appears before all three starts.
    seen_starts: set[str] = set()
    for kind, name in events:
        if kind == "start":
            seen_starts.add(name)
        else:
            assert seen_starts == {"facts", "pattern", "compliance"}, events


def test_agent_nodes_ignore_sibling_keys(db_url: str) -> None:
    """Each node only needs question/pii_types; sibling keys must not be required."""
    from app.response.agents.compliance import compliance_node
    from app.response.agents.facts import facts_node
    from app.response.agents.pattern import pattern_node

    base = {"question": "What is the exit load on HDFC Large Cap?", "pii_types": []}
    # Strip other agent keys before calling (plan test).
    facts_out = facts_node({**base})
    pattern_out = pattern_node({**base})
    compliance_out = compliance_node({**base})
    assert facts_out["facts"]["covered"] is True
    assert pattern_out["pattern"]["theme_id"] == "fees_brokerage"
    assert "advice" in compliance_out["compliance"]


def test_malformed_cuesonly_still_structured(db_url: str, compiled) -> None:
    llm_mod._CLIENT = LLMClient(backend=CuesOnlyBackend(fuzz="{{{not-json"))
    reset_call_counts()
    with session_scope(db_url) as session:
        ticket = create_ticket(
            "HDFC Large Cap exit load?",
            session=session,
            compiled=compiled,
        )
        rows = session.scalars(
            select(models.AgentOutput).where(models.AgentOutput.ticket_id == ticket.id)
        ).all()
    assert len(rows) == 3
    agents = {r.agent for r in rows}
    assert agents == {"facts", "pattern", "compliance"}
    for row in rows:
        assert isinstance(row.written_output, dict)
        assert row.written_output


def test_checkpoint_blob_has_no_planted_pan(db_url: str, tmp_path: Path) -> None:
    ckpt = tmp_path / "ckpt_pii.sqlite3"
    compiled = compile_graph(path=ckpt)
    with session_scope(db_url) as session:
        create_ticket(
            f"Exit load on HDFC Large Cap. PAN {PLANTED_PAN}",
            session=session,
            compiled=compiled,
            ticket_id="t_pii_ckpt",
        )
    raw = ckpt.read_bytes()
    assert PLANTED_PAN.encode() not in raw
    # Also grep via sqlite dump of text columns
    conn = sqlite3.connect(str(ckpt))
    try:
        for table in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            name = table[0]
            rows = conn.execute(f"SELECT * FROM [{name}]").fetchall()
            for row in rows:
                for cell in row:
                    if isinstance(cell, (str, bytes)):
                        blob = cell if isinstance(cell, bytes) else cell.encode()
                        assert PLANTED_PAN.encode() not in blob
    finally:
        conn.close()


def test_facts_out_of_section_lockin(db_url: str) -> None:
    assert detect_out_of_section("What's the ELSS lock-in period?") == "lock-in"
    out, _ = run_facts("What's the ELSS lock-in period?")
    assert out["covered"] is False


def test_facts_multi_scheme(db_url: str) -> None:
    q = "expense ratio of large cap vs mid cap"
    assert set(detect_schemes(q)) >= {"large_cap", "mid_cap"}
    out, _ = run_facts(q)
    assert out.get("multi_scheme")
    assert len(out["multi_scheme"]) >= 2
    # Answers at most one scheme
    if out["covered"]:
        assert out["scheme_id"] in out["multi_scheme"]


def test_facts_family_exit_load(db_url: str) -> None:
    out, _ = run_facts("What is the exit load?")
    # scheme null + section Exit load → equity family when texts match
    assert out["covered"] is True
    assert out.get("family") == "equity"
    assert "large_cap" in (out.get("family_schemes") or [])


def test_compliance_ors_regex(db_url: str) -> None:
    out, meta = run_compliance("Should I redeem HDFC Large Cap?")
    assert out["advice"] is True
    out2, _ = run_compliance("What returns should I expect from HDFC Mid Cap?")
    assert out2["performance_claim"] is True


def test_seed_questions_file_has_about_40() -> None:
    items = load_seed_file()
    assert 38 <= len(items) <= 45
    tags = {t for item in items for t in item.get("tags", [])}
    assert "m3_complaint" in tags
    assert "exit_load_repeat" in tags
    assert "out_of_scope" in tags
    assert "adversarial" in tags


def test_all_seed_questions_three_agent_outputs(db_url: str, compiled) -> None:
    items = load_seed_file()
    with session_scope(db_url) as session:
        ids = seed_tickets(session, compiled=compiled)
        assert len(ids) == len(items)
        for tid in ids:
            rows = session.scalars(
                select(models.AgentOutput).where(models.AgentOutput.ticket_id == tid)
            ).all()
            assert len(rows) == 3
            agents = {r.agent for r in rows}
            assert agents == {"facts", "pattern", "compliance"}
            for row in rows:
                assert row.written_output is not None
                # validated_choice is allow-list id, null-ish string, or composite
                assert row.validated_choice is None or isinstance(row.validated_choice, str)


def test_planted_pan_never_in_db_text(db_url: str, compiled) -> None:
    with session_scope(db_url) as session:
        ticket = create_ticket(
            f"Why exit load? PAN {PLANTED_PAN}",
            session=session,
            compiled=compiled,
        )
        assert PLANTED_PAN not in ticket.question_redacted
        assert "[PAN]" in ticket.question_redacted
        for row in ticket.agent_outputs:
            blob = json.dumps(row.written_output)
            assert PLANTED_PAN not in blob
