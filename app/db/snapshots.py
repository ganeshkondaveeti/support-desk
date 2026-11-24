"""Canonical DB snapshot + diff for edit-correctness evals (architecture §15.2)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db import models

# Volatile timestamp columns excluded from the canonical snapshot.
_VOLATILE = frozenset(
    {
        "started_at",
        "finished_at",
        "received_at",
        "created_at",
        "activated_at",
        "superseded_at",
        "decided_at",
        "executed_at",
        "sent_at",
        "ts",
        "first_seen",
        "last_seen",
    }
)

_TABLES: tuple[tuple[str, type[Any]], ...] = (
    ("weekly_runs", models.WeeklyRun),
    ("theme_snapshots", models.ThemeSnapshot),
    ("pulses", models.Pulse),
    ("fee_explainers", models.FeeExplainer),
    ("canonical_explainers", models.CanonicalExplainer),
    ("tickets", models.Ticket),
    ("agent_outputs", models.AgentOutput),
    ("draft_versions", models.DraftVersion),
    ("refinements", models.Refinement),
    ("question_clusters", models.QuestionCluster),
    ("proposed_actions", models.ProposedAction),
    ("audit_events", models.AuditEvent),
    ("outbox_replies", models.OutboxReply),
)


@dataclass(frozen=True, slots=True)
class Diff:
    added: dict[str, list[Any]] = field(default_factory=dict)
    removed: dict[str, list[Any]] = field(default_factory=dict)
    changed: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.added and not self.removed and not self.changed

    def to_dict(self) -> dict[str, Any]:
        return {"added": self.added, "removed": self.removed, "changed": self.changed}


def snapshot(db: Session) -> dict[str, list[dict[str, Any]]]:
    """Canonical JSON of all tables; volatile timestamps excluded."""
    db.flush()
    out: dict[str, list[dict[str, Any]]] = {}
    for name, model in _TABLES:
        rows = db.query(model).all()
        serialized = [_row_to_dict(row) for row in rows]
        serialized.sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
        out[name] = serialized
    return out


def diff(a: dict[str, Any], b: dict[str, Any]) -> Diff:
    """Structural diff of two snapshots keyed by table then row id."""
    added: dict[str, list[Any]] = {}
    removed: dict[str, list[Any]] = {}
    changed: dict[str, list[dict[str, Any]]] = {}

    tables = sorted(set(a) | set(b))
    for table in tables:
        left = {_row_key(table, row): row for row in a.get(table, [])}
        right = {_row_key(table, row): row for row in b.get(table, [])}
        for key in sorted(set(right) - set(left)):
            added.setdefault(table, []).append(right[key])
        for key in sorted(set(left) - set(right)):
            removed.setdefault(table, []).append(left[key])
        for key in sorted(set(left) & set(right)):
            if left[key] != right[key]:
                changed.setdefault(table, []).append(
                    {"key": key, "before": left[key], "after": right[key]}
                )
    return Diff(added=added, removed=removed, changed=changed)


def _row_to_dict(row: object) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for col in row.__table__.columns:  # type: ignore[attr-defined]
        if col.name in _VOLATILE:
            continue
        value = getattr(row, col.name)
        data[col.name] = _normalize(value)
    return data


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _row_key(table: str, row: dict[str, Any]) -> str:
    if "id" in row:
        return str(row["id"])
    if table == "theme_snapshots":
        return f"{row.get('run_id')}:{row.get('theme_id')}"
    if table == "agent_outputs":
        return f"{row.get('ticket_id')}:{row.get('agent')}:{row.get('attempt')}"
    if table == "question_clusters":
        return str(row.get("fingerprint"))
    return json.dumps(row, sort_keys=True, default=str)
