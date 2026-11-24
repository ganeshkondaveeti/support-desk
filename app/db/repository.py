"""PII-safe repository writes (architecture §5.3)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from app.db import models
from app.guardrails.pii import scan


class PIILeakError(RuntimeError):
    """Raised when a repository write would persist raw PII."""

    def __init__(self, types: list[str], column: str) -> None:
        self.types = types
        self.column = column
        super().__init__(f"PIILeakError on {column}: {types}")


_TEXT_COLUMNS: dict[type[Any], tuple[str, ...]] = {
    models.Ticket: ("question_redacted",),
    models.Pulse: ("body_md",),
    models.DraftVersion: ("reply_text", "disagreement"),
    models.Refinement: ("hint_redacted",),
    models.QuestionCluster: ("representative_text",),
    models.ProposedAction: ("payload",),
    models.OutboxReply: ("reply_text",),
    models.WeeklyRun: ("error",),
    models.ThemeSnapshot: ("name", "description"),
    models.FeeExplainer: ("last_checked",),
    models.AuditEvent: (),  # details JSON scanned separately
}


# Review ids are 16-char hex and fingerprints 40-char hex. Some look like a PAN or a
# phone number to the scanner (11 of the 1,650 real review ids do), which would fail
# a pulse or a draft that cites them. They are identifiers, not user text, so only
# these exact positions are excluded: the pulse's "review <id>" citation and JSON
# values under identifier keys. Everything else is scanned as before.
_REVIEW_CITATION = re.compile(r"\breview [0-9a-f]{16}\b")
_ID_KEYS = frozenset({"review_id", "review_ids", "review_ids_sample", "fingerprint"})


def _mask_identifiers(text: str) -> str:
    return _REVIEW_CITATION.sub("review <id>", text)


def scan_text_fields(entity: object) -> None:
    """Run pii.scan on every free-text column; raise PIILeakError on a hit."""
    columns = _TEXT_COLUMNS.get(type(entity), ())
    for name in columns:
        value = getattr(entity, name, None)
        if isinstance(value, str) and value:
            found = scan(_mask_identifiers(value))
            if found:
                raise PIILeakError(found, f"{type(entity).__name__}.{name}")
    # JSON blobs that may embed free text
    if isinstance(entity, models.Pulse):
        _scan_json_strings(entity.quotes, "Pulse.quotes")
        _scan_json_strings(entity.action_ideas, "Pulse.action_ideas")
        _scan_json_strings(entity.repeat_questions, "Pulse.repeat_questions")
    if isinstance(entity, models.FeeExplainer):
        _scan_json_strings(entity.bullets, "FeeExplainer.bullets")
    if isinstance(entity, models.AuditEvent):
        _scan_json_strings(entity.details, "AuditEvent.details")
    if isinstance(entity, models.DraftVersion):
        _scan_json_strings(entity.routing_note, "DraftVersion.routing_note")
        _scan_json_strings(entity.reviewer_result, "DraftVersion.reviewer_result")


def add(session: Session, entity: object) -> object:
    scan_text_fields(entity)
    session.add(entity)
    return entity


def add_all(session: Session, entities: Iterable[object]) -> None:
    for entity in entities:
        add(session, entity)


def _scan_json_strings(value: object, column: str) -> None:
    if value is None:
        return
    if isinstance(value, str):
        found = scan(_mask_identifiers(value))
        if found:
            raise PIILeakError(found, column)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _ID_KEYS:
                continue
            _scan_json_strings(item, column)
        return
    if isinstance(value, list):
        for item in value:
            _scan_json_strings(item, column)
