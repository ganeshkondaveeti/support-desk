"""Snapshot / diff helpers for Suite 2 (eval.md §5.1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.db.session import session_scope
from app.db.snapshots import Diff, diff, snapshot

_BINDING = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def take_snapshot(db_url: str) -> dict[str, list[dict[str, Any]]]:
    with session_scope(db_url) as session:
        return snapshot(session)


def diff_snapshots(a: dict[str, Any], b: dict[str, Any]) -> Diff:
    return diff(a, b)


def _apply_bindings(value: Any, bindings: dict[str, str]) -> Any:
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            return bindings.get(key, match.group(0))

        return _BINDING.sub(repl, value)
    if isinstance(value, list):
        return [_apply_bindings(v, bindings) for v in value]
    if isinstance(value, dict):
        return {k: _apply_bindings(v, bindings) for k, v in value.items()}
    return value


def _reviewer_flags(row: dict[str, Any]) -> list[str] | None:
    """Flags live in draft_versions.reviewer_result (failures + flags), stored as JSON."""
    direct = row.get("reviewer_flags") or row.get("flags")
    if isinstance(direct, list):
        return [str(f) for f in direct]
    result = row.get("reviewer_result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, dict):
        return None
    return [str(f) for f in (result.get("failures") or []) + (result.get("flags") or [])]


def _row_matches_filters(row: dict[str, Any], criteria: dict[str, Any]) -> bool:
    skip = {"min_count", "count", "entity_id", "reviewer_flags_include", "reply_text_contains"}
    for key, expected in criteria.items():
        if key in skip:
            continue
        if row.get(key) != expected:
            return False
    if "entity_id" in criteria and row.get("entity_id") != criteria["entity_id"]:
        return False
    if "reviewer_flags_include" in criteria:
        flags = _reviewer_flags(row)
        if flags is None:
            return False
        for f in criteria["reviewer_flags_include"]:
            if f not in flags:
                return False
    if "reply_text_contains" in criteria:
        text = str(row.get("reply_text") or "")
        if criteria["reply_text_contains"] not in text:
            return False
    return True


def _find_changed_entry(
    table: str,
    spec_entry: dict[str, Any],
    delta: Diff,
) -> dict[str, Any] | None:
    changes = delta.changed.get(table, [])
    if "id" in spec_entry:
        want_id = str(spec_entry["id"])
        for ch in changes:
            after = ch.get("after") or {}
            if str(after.get("id")) == want_id:
                return ch
        return None
    subject_id = spec_entry.get("subject_id")
    kind = spec_entry.get("kind")
    if subject_id is not None:
        for ch in changes:
            after = ch.get("after") or {}
            if str(after.get("subject_id")) != str(subject_id):
                continue
            if kind is not None and after.get("kind") != kind:
                continue
            return ch
    if spec_entry.get("only_pending_weekly_actions"):
        pending = [
            ch
            for ch in changes
            if (ch.get("after") or {}).get("status") == "superseded"
            and (ch.get("before") or {}).get("status") == "pending"
        ]
        return pending[0] if pending else None
    return None


def _check_status_transition(
    change: dict[str, Any],
    want: list[str],
) -> bool:
    before = (change.get("before") or {}).get("status")
    after = (change.get("after") or {}).get("status")
    return [before, after] == want


def _check_added(table: str, spec: dict[str, Any], delta: Diff) -> tuple[bool, str]:
    added = list(delta.added.get(table, []))
    want_count = spec.get("count")
    min_count = spec.get("min_count", want_count)
    filtered = [r for r in added if _row_matches_filters(r, spec)]
    if want_count is not None and len(filtered) != int(want_count):
        return False, f"{table}: expected {want_count} added rows, got {len(filtered)}"
    if min_count is not None and len(filtered) < int(min_count):
        return False, f"{table}: expected ≥{min_count} added rows, got {len(filtered)}"
    return True, ""


def _check_changed_table(table: str, table_spec: dict[str, Any], delta: Diff) -> tuple[bool, str]:
    for entry in table_spec.get("changed", []):
        if entry.get("only_pending_weekly_actions"):
            changes = delta.changed.get(table, [])
            pending_changed = [
                ch
                for ch in changes
                if (ch.get("before") or {}).get("status") == "pending"
                and (ch.get("after") or {}).get("status") == "superseded"
            ]
            if not pending_changed:
                return False, f"{table}: expected pending weekly actions to be superseded"
            for ch in changes:
                before = ch.get("before") or {}
                if before.get("status") in {"executed", "rejected"}:
                    if before != ch.get("after"):
                        return False, f"{table}: executed/rejected action row changed"
            continue
        ch = _find_changed_entry(table, entry, delta)
        if ch is None:
            return False, f"{table}: no matching changed row for {entry!r}"
        if "status_includes" in entry:
            after_status = (ch.get("after") or {}).get("status")
            allowed = {str(s) for s in entry["status_includes"]}
            if str(after_status) not in allowed:
                return (
                    False,
                    f"{table}: status {after_status!r} not in {entry['status_includes']}",
                )
        elif "status" in entry and not _check_status_transition(ch, entry["status"]):
            b = (ch.get("before") or {}).get("status")
            a = (ch.get("after") or {}).get("status")
            return False, f"{table}: status transition {b!r} → {a!r}, want {entry['status']}"
        for field in entry.get("fields_include", []):
            before = ch.get("before") or {}
            after = ch.get("after") or {}
            if field not in before and field not in after:
                continue
            if before.get(field) == after.get(field):
                return False, f"{table}: field {field!r} did not change"
    return True, ""


def _table_is_quiet(delta: Diff, table: str) -> bool:
    return (
        not delta.added.get(table) and not delta.removed.get(table) and not delta.changed.get(table)
    )


def _table_is_quiet_for_other_tables(delta: Diff, table: str) -> bool:
    """Concurrent eval traffic may only ``changed`` peripheral rows."""
    if delta.added.get(table) or delta.removed.get(table):
        return False
    if not delta.changed.get(table):
        return True
    return table in {"proposed_actions", "tickets", "draft_versions", "agent_outputs"}


def match_expected(
    before: dict[str, Any],
    after: dict[str, Any],
    spec: dict[str, Any],
    bindings: dict[str, str],
    samples_dir: Path,
) -> tuple[bool, str]:
    """Assert ``diff(before, after)`` matches the expected-diff fixture."""
    bound = _apply_bindings(spec, bindings)
    delta = diff(before, after)

    if bound.get("empty_diff"):
        if not delta.is_empty():
            return False, f"expected empty diff, got {delta.to_dict()}"
        return True, ""

    tables_spec: dict[str, Any] = bound.get("tables") or {}
    for table, table_spec in tables_spec.items():
        if not isinstance(table_spec, dict):
            continue
        if "added" in table_spec:
            ok, msg = _check_added(table, table_spec["added"], delta)
            if not ok:
                return False, msg
        ok, msg = _check_changed_table(table, table_spec, delta)
        if not ok:
            return False, msg

    must_not_add = bound.get("proposed_actions_must_not_add")
    if must_not_add:
        added = delta.added.get("proposed_actions", [])
        for row in added:
            if _row_matches_filters(row, must_not_add):
                return False, "proposed_actions unexpectedly added matching row"

    for table in bound.get("tables_unchanged", []):
        if not _table_is_quiet(delta, str(table)):
            return False, f"table {table!r} was expected unchanged"

    if bound.get("all_other_tables") == "unchanged":
        mentioned = set(tables_spec)
        _ignore_other = frozenset({"audit_events", "question_clusters"})
        all_tables = set(before.keys()) | set(after.keys())
        for table in sorted(all_tables):
            if table in mentioned:
                continue
            if table in _ignore_other:
                continue
            if not _table_is_quiet_for_other_tables(delta, table):
                added = delta.to_dict().get("added", {}).get(table, [])
                changed = delta.changed.get(table, [])
                sample = (added or changed)[:1]
                snippet = json.dumps(sample, default=str)[:200]
                return False, f"all_other_tables: {table} had changes: {snippet}"

    for rel_path, action in (bound.get("files") or {}).items():
        target = _map_sample_path(samples_dir, str(rel_path))
        if action == "created":
            if not target.is_file():
                return False, f"expected file created: {rel_path}"
        else:
            return False, f"unsupported file action {action!r} for {rel_path}"

    for action_id in bound.get("unchanged_action_ids", []):
        if not action_id:
            continue
        before_row = _row_by_id(before, "proposed_actions", str(action_id))
        after_row = _row_by_id(after, "proposed_actions", str(action_id))
        if before_row is None or after_row is None:
            return False, f"unchanged_action_ids: missing action {action_id!r}"
        if before_row != after_row:
            return False, f"action {action_id!r} changed unexpectedly"

    for action_id in bound.get("action_status_unchanged", []):
        before_row = _row_by_id(before, "proposed_actions", str(action_id))
        after_row = _row_by_id(after, "proposed_actions", str(action_id))
        if before_row is None or after_row is None:
            return False, f"action_status_unchanged: missing action {action_id!r}"
        if before_row.get("status") != after_row.get("status"):
            return False, f"action {action_id!r} status changed"

    if bound.get("executed_and_rejected_actions_unchanged"):
        for row in before.get("proposed_actions", []):
            if row.get("status") not in {"executed", "rejected"}:
                continue
            aid = str(row.get("id"))
            after_row = _row_by_id(after, "proposed_actions", aid)
            if after_row != row:
                return False, f"executed/rejected action {aid} changed"

    literal = bound.get("body_must_not_persist_literal")
    if literal:
        lit = str(literal.get("value") or "")
        for row in after.get("draft_versions", []):
            if lit in str(row.get("reply_text") or ""):
                return False, f"literal {literal.get('type')} persisted in draft_versions"

    return True, ""


def _row_by_id(
    snap: dict[str, Any],
    table: str,
    row_id: str,
) -> dict[str, Any] | None:
    for row in snap.get(table, []):
        if str(row.get("id")) == row_id:
            return row
    return None


def _map_sample_path(samples_dir: Path, rel: str) -> Path:
    rel = rel.removeprefix("samples/")
    return samples_dir / rel
