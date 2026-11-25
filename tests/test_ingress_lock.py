"""Ticket ingress mustn't hold SQLite's write lock while the agents call the model.

Found during the Groq-graded eval run (P12.5): with calls waiting for rate-limit
capacity, concurrent POST /api/tickets failed with "database is locked".
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from app.corpus.loader import load_corpus
from app.db import models
from app.db.session import init_db, reset_engine, session_scope
from app.llm import client as llm_mod
from app.llm.client import CuesOnlyBackend, LLMClient, reset_llm_client
from app.response import ingress
from app.response.graph import compile_graph, reset_graph
from app.response.ingress import create_ticket
from sqlalchemy import func, select


@pytest.fixture()
def db_url(tmp_path: Path):
    reset_engine()
    reset_graph()
    reset_llm_client()
    llm_mod._CLIENT = LLMClient(backend=CuesOnlyBackend())
    load_corpus()
    url = f"sqlite:///{tmp_path / 'lock.sqlite3'}"
    init_db(url)
    yield url
    reset_engine()
    reset_graph()
    reset_llm_client()


def _ticket_count(url: str) -> int:
    with session_scope(url) as session:
        return int(session.scalar(select(func.count()).select_from(models.Ticket)) or 0)


def test_other_writes_proceed_while_agents_wait_on_the_model(
    db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    original = LLMClient.choose

    def slow_choose(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        entered.set()
        release.wait(timeout=15)  # a slow model call / a rate-limit wait
        return original(self, *args, **kwargs)

    monkeypatch.setattr(LLMClient, "choose", slow_choose)
    compiled = compile_graph(path=tmp_path / "checkpoints.sqlite3")
    errors: list[BaseException] = []

    def first_ticket() -> None:
        try:
            with session_scope(db_url) as session:
                create_ticket(
                    "What is the exit load on HDFC Large Cap?", session=session, compiled=compiled
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=first_ticket)
    worker.start()
    try:
        assert entered.wait(timeout=15), "agents never called the model"
        started = time.monotonic()
        with session_scope(db_url) as other:
            create_ticket("A second question", session=other, run_graph=False)
        # Before the fix this waited out SQLite's 5 s busy timeout, then failed.
        assert time.monotonic() - started < 2.0
    finally:
        release.set()
        worker.join(timeout=30)

    assert not errors, errors
    assert _ticket_count(db_url) == 2
    with session_scope(db_url) as session:
        statuses = set(session.scalars(select(models.Ticket.status)).all())
    assert statuses == {"processing", "awaiting_human"}  # the run_graph=False row stays processing


def test_a_failed_graph_leaves_no_ticket(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_kwargs: object) -> dict:
        raise RuntimeError("graph failed")

    monkeypatch.setattr(ingress, "invoke_ticket", boom)
    with pytest.raises(RuntimeError), session_scope(db_url) as session:
        create_ticket("What is the exit load on HDFC Large Cap?", session=session)
    assert _ticket_count(db_url) == 0
