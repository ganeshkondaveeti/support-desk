"""Eval results JSON + latest.md (eval.md §8)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

SUITE_LABELS = {
    "feasibility": "1. Feasibility",
    "edit": "2. Correctness of edits",
    "grounding": "3. Grounding & compliance",
    "quality": "4. Agent quality",
}


def write_results(
    results_dir: Path, payload: dict[str, Any], *, stem: str | None = None
) -> tuple[Path, Path]:
    """Write ``{timestamp}.json`` (or ``{stem}.json``) and ``latest.md`` under ``results_dir``."""
    results_dir.mkdir(parents=True, exist_ok=True)
    started = (payload.get("run") or {}).get("started_at")
    if isinstance(started, str):
        try:
            ts = datetime.fromisoformat(started)
        except ValueError:
            ts = datetime.now(tz=IST)
    else:
        ts = datetime.now(tz=IST)
    stamp = ts.astimezone(IST).strftime("%Y%m%d-%H%M%S")
    json_path = results_dir / f"{stem or stamp}.json"
    md_path = results_dir / "latest.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_latest_md(payload), encoding="utf-8")
    return json_path, md_path


def render_latest_md(payload: dict[str, Any]) -> str:
    """Render ``latest.md`` per eval.md §8.2."""
    run = payload.get("run") or {}
    started = run.get("started_at", "")
    title_when = _format_title_time(started)
    mode = run.get("mode", "ci")
    backend = run.get("backend", "cues")
    model = run.get("model", "n/a")
    seed = run.get("seed", "")
    git_sha = run.get("git_sha", "")

    lines: list[str] = [
        f"# Evaluation results — {title_when}",
        f"Mode: {mode} · Backend: {backend} / {model} · Seed {seed} · git {git_sha}",
        "Facts-only. No investment advice.",
        "",
        *_notes_block(run),
        "| Suite | Cases | Passed | Status |",
        "|---|---:|---:|---|",
    ]

    suites: dict[str, Any] = payload.get("suites") or {}
    for key, label in SUITE_LABELS.items():
        suite = suites.get(key) or {}
        cases = suite.get("cases") or []
        case_count = len(cases) if cases else suite.get("case_count", "—")
        passed = _suite_passed_count(suite, key)
        status = _status_glyph(suite.get("status", ""), key)
        lines.append(f"| {label} | {case_count} | {passed} | {status} |")

    lines.extend(
        [
            "",
            _headline_block(payload),
            "",
            "## Failures",
            _failures_block(suites),
            "",
            "## Per-case detail",
        ]
    )
    lines.append(_per_case_detail(suites))
    lines.append("")
    return "\n".join(lines)


def _notes_block(run: dict[str, Any]) -> list[str]:
    notes = run.get("notes") or []
    if not notes:
        return []
    return ["**Notes**", "", *(f"- {n}" for n in notes), ""]


def _format_title_time(started: Any) -> str:
    if not started:
        return datetime.now(tz=IST).strftime("%Y-%m-%d %H:%M IST")
    try:
        dt = datetime.fromisoformat(str(started))
        return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
    except ValueError:
        return str(started)


def _suite_passed_count(suite: dict[str, Any], key: str) -> str:
    if key == "quality":
        return "—"
    cases = suite.get("cases") or []
    if not cases:
        return str(suite.get("passed", "—"))
    passed = sum(1 for c in cases if c.get("status") == "pass")
    return str(passed)


def _status_glyph(status: str, key: str) -> str:
    if key == "quality":
        return "ℹ️"
    if status == "pass":
        return "✅"
    if status == "fail":
        return "❌"
    if status == "skip":
        return "⏭️"
    return "ℹ️"


def _headline_block(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    suites = payload.get("suites") or {}
    g = suites.get("grounding") or {}
    for case in g.get("cases") or []:
        if case.get("id") == "GC-01":
            traced = case.get("metric", {}).get("traced", case.get("traced"))
            sampled = case.get("metric", {}).get("sampled", case.get("sampled"))
            if traced is not None and sampled is not None:
                parts.append(f"{traced}/{sampled} sampled claims traced")
    sweep = payload.get("pii_sweep") or {}
    hits = sweep.get("hits", 0)
    locs = sweep.get("locations_scanned", 0)
    parts.append(f"PII sweep: {hits} hits in {locs} locations")
    feas = suites.get("feasibility") or {}
    for case in feas.get("cases") or []:
        if case.get("id") == "FEAS-03":
            rate = case.get("metric", {}).get("error_rate")
            if rate is not None:
                parts.append(f"Agent ERROR rate {rate}")
    qual = suites.get("quality") or {}
    for case in qual.get("cases") or []:
        if case.get("id") == "AQ-01" and case.get("value") is not None:
            parts.append(f"Facts pick accuracy {case['value']}")
    if not parts:
        return "Headline numbers: (see per-case detail)"
    return "Headline numbers: " + " · ".join(parts)


def _failures_block(suites: dict[str, Any]) -> str:
    rows: list[str] = []
    for suite in suites.values():
        for case in suite.get("cases") or []:
            if case.get("status") == "fail":
                msg = case.get("message") or ""
                rows.append(f"- **{case.get('id')}**: {msg}")
    return "(none)" if not rows else "\n".join(rows)


def _per_case_detail(suites: dict[str, Any]) -> str:
    lines: list[str] = []
    for sname, suite in suites.items():
        cases = suite.get("cases") or []
        if not cases:
            continue
        lines.append(f"### {sname}")
        for case in cases:
            cid = case.get("id", "?")
            status = case.get("status", "")
            metric = case.get("metric") or {}
            extra = ", ".join(f"{k}={v}" for k, v in metric.items() if v is not None)
            detail = f"- {cid}: {status}"
            if extra:
                detail += f" ({extra})"
            if case.get("message"):
                detail += f" — {case['message']}"
            lines.append(detail)
        lines.append("")
    return "\n".join(lines).rstrip() if lines else "…"
