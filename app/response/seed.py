"""Load data/seed_questions.json through the real ingress path."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.client import rules_only
from app.response.ingress import create_ticket

ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = ROOT / "data" / "seed_questions.json"


def load_seed_file(path: Path | None = None) -> list[dict[str, Any]]:
    data = json.loads((path or SEED_PATH).read_text(encoding="utf-8"))
    questions = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(questions, list):
        raise ValueError("seed_questions.json must contain a questions list")
    return questions


def seed_tickets(
    session: Session,
    *,
    path: Path | None = None,
    compiled: Any = None,
    limit: int | None = None,
) -> list[str]:
    """Create tickets for each seed question (redacted on load). Returns ticket ids."""
    items = load_seed_file(path)
    if limit is not None:
        items = items[:limit]
    ids: list[str] = []
    with seed_llm_scope():
        _create_all(session, items, ids, compiled)
    return ids


def seed_llm_scope() -> AbstractContextManager[None]:
    """Seed tickets use the rules unless LLM_FOR_SEED is set (saves the model quota)."""
    return nullcontext() if get_settings().llm_for_seed else rules_only()


def _create_all(
    session: Session, items: list[dict[str, Any]], ids: list[str], compiled: Any
) -> None:
    for item in items:
        text = item["text"]
        ticket = create_ticket(
            text,
            channel=str(item.get("channel") or "seed"),
            session=session,
            ticket_id=str(item["id"]) if item.get("id") else None,
            compiled=compiled,
        )
        del text
        ids.append(ticket.id)
