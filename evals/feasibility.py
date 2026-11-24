"""Suite 1 — Feasibility checks (eval.md §4)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any

from app.db import models
from app.db.session import session_scope
from app.guardrails.length import count_sentences, count_words
from app.weekly.cluster import baseline_counts
from app.weekly.trends import week_ending_as_of_values
from sqlalchemy import select

from .client import EvalHttpError
from .fixtures_loader import load_jsonl
from .types import CaseResult, EvalContext, SuiteResult

ROOT = Path(__file__).resolve().parent.parent
REVIEWS_PATH = ROOT / "data" / "normalized_reviews.json"
TERMINAL_STATUSES = frozenset({"awaiting_human", "blocked"})
AGENTS = ("facts", "pattern", "compliance")
BASELINE_ORDER = (
    "trading_charts",
    "money_movement",
    "reliability",
    "fees_brokerage",
    "other",
)

_CONTROL_QUESTIONS = (
    "What is the benchmark for HDFC Large Cap Fund?",
    "Which index does HDFC Large Cap track as its benchmark?",
    "HDFC Large Cap benchmark name please?",
    "Tell me the benchmark of HDFC Large Cap.",
)

_SETUP_FIXTURES = (
    ("m3_complaints", "DQ-1"),
    ("exit_load_repeats", "DQ-2"),
    ("fee_distinct", "DQ-3"),
    ("benign_facts", "DQ-9"),
)


def _case_pass(case_id: str, **metric: Any) -> CaseResult:
    return CaseResult(id=case_id, status="pass", metric=metric)


def _case_fail(case_id: str, message: str, **kw: Any) -> CaseResult:
    return CaseResult(id=case_id, status="fail", message=message, **kw)


def _case_skip(case_id: str, message: str) -> CaseResult:
    return CaseResult(id=case_id, status="skip", message=message)


def _filter_cases(
    case_ids: list[str],
    case_filter: str | None,
) -> list[str]:
    if not case_filter:
        return case_ids
    upper = case_filter.upper()
    return [c for c in case_ids if c.upper() == upper]


def _submit_fixture_rows(
    ctx: EvalContext,
    rows: list[dict[str, Any]],
    *,
    workers: int = 4,
) -> None:
    def _one(row: dict[str, Any]) -> tuple[str, str]:
        created = ctx.client.create_ticket(str(row["text"]))
        return str(row["id"]), str(created["id"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, row) for row in rows]
        for fut in as_completed(futures):
            fid, tid = fut.result()
            ctx.ticket_map[fid] = tid


def _wait_tickets_terminal(ctx: EvalContext, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending: list[str] = []
        for tid in set(ctx.ticket_map.values()):
            st = ctx.client.get_ticket(tid).get("status")
            if st not in TERMINAL_STATUSES:
                pending.append(str(tid))
        if not pending:
            return
        time.sleep(0.5)
    raise TimeoutError(f"tickets not terminal after {timeout}s: {pending[:5]}")


def _wait_weekly_status(
    ctx: EvalContext,
    run_id: str,
    want: str = "awaiting_approval",
    timeout: float = 120.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = ctx.client.get_weekly_run(run_id)
        if last.get("status") == want:
            return last
        if last.get("status") == "failed":
            return last
        time.sleep(0.5)
    return last


def _bind_weekly_actions(ctx: EvalContext, run_id: str) -> None:
    detail = ctx.client.get_weekly_run(run_id)
    pulse_id = (detail.get("pulse") or {}).get("id")
    expl_id = (detail.get("explainer") or {}).get("id")
    if not pulse_id or not expl_id:
        raise RuntimeError(f"weekly run {run_id} missing pulse or explainer")

    items = ctx.client.list_actions(status_filter="")["items"]
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        sid = str(item.get("subject_id"))
        by_subject.setdefault(sid, []).append(item)

    def _pick(subject_id: str, kind: str) -> str | None:
        for row in by_subject.get(subject_id, []):
            if row.get("kind") == kind and row.get("status") == "pending":
                return str(row["id"])
        for row in by_subject.get(subject_id, []):
            if row.get("kind") == kind:
                return str(row["id"])
        return None

    ctx.pulse_doc_action_id = _pick(str(pulse_id), "doc_append")
    ctx.pulse_email_action_id = _pick(str(pulse_id), "email_draft")
    ctx.expl_doc_action_id = _pick(str(expl_id), "doc_append")
    ctx.expl_email_action_id = _pick(str(expl_id), "email_draft")

    ctx.weekly_run_id = run_id
    ctx.bindings.update(
        {
            "weekly_run_id": run_id,
            "pulse_doc": ctx.pulse_doc_action_id or "",
            "pulse_email": ctx.pulse_email_action_id or "",
            "expl_doc": ctx.expl_doc_action_id or "",
            "expl_email": ctx.expl_email_action_id or "",
        }
    )


def _sync_bindings_from_ctx(ctx: EvalContext) -> None:
    ctx.bindings.update(
        {
            "pulse_doc": ctx.pulse_doc_action_id or ctx.bindings.get("pulse_doc", ""),
            "pulse_email": ctx.pulse_email_action_id or ctx.bindings.get("pulse_email", ""),
            "expl_doc": ctx.expl_doc_action_id or ctx.bindings.get("expl_doc", ""),
            "expl_email": ctx.expl_email_action_id or ctx.bindings.get("expl_email", ""),
            "weekly_run_id": ctx.weekly_run_id or ctx.bindings.get("weekly_run_id", ""),
        }
    )


def _ticket_row(detail: dict[str, Any]) -> int | None:
    draft = detail.get("current_draft") or {}
    row = draft.get("row")
    if isinstance(row, int):
        return row
    result = draft.get("reviewer_result") or {}
    raw = result.get("row")
    return int(raw) if isinstance(raw, int) else None


def _pick_edit_ticket_bindings(ctx: EvalContext) -> None:
    """Choose T1–T6 for the edit suite (eval.md §5.2)."""
    reserved: set[str] = set()
    for key in ("feas11_fee_ticket",):
        tid = ctx.bindings.get(key)
        if tid:
            reserved.add(str(tid))
    for tid in ctx.bindings.get("control_ticket_ids", "").split(","):
        if tid:
            reserved.add(tid)

    candidates: list[tuple[str, dict[str, Any]]] = []
    for fid, tid in ctx.ticket_map.items():
        if tid in reserved:
            continue
        if not fid.startswith("DQ9-"):
            continue
        detail = ctx.client.get_ticket(tid)
        if detail.get("status") != "awaiting_human":
            continue
        candidates.append((tid, detail))

    def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, int, str]:
        tid, detail = item
        row = _ticket_row(detail) or 99
        has_send = 1 if detail.get("pending_reply_send_id") else 0
        return (0 if row == 4 else 1, -has_send, tid)

    candidates.sort(key=_sort_key)
    if len(candidates) < 6:
        for _fid, tid in ctx.ticket_map.items():
            if tid in reserved or any(tid == c[0] for c in candidates):
                continue
            detail = ctx.client.get_ticket(tid)
            if detail.get("status") == "awaiting_human":
                candidates.append((tid, detail))
            if len(candidates) >= 6:
                break

    if len(candidates) < 4:
        raise RuntimeError("not enough awaiting_human tickets for T1–T6")

    picks = [c[0] for c in candidates[:6]]
    while len(picks) < 6:
        picks.append(picks[-1])

    ctx.bindings["t1"] = picks[0]
    ctx.bindings["t2"] = picks[1]
    ctx.bindings["t3"] = picks[2]
    ctx.bindings["t4"] = picks[3]
    ctx.bindings["t5"] = picks[4]
    ctx.bindings["t6"] = picks[5]


def setup_feasibility(ctx: EvalContext) -> None:
    """Seed queue, weekly run, and edit-suite ticket bindings."""
    rows: list[dict[str, Any]] = []
    for name, _label in _SETUP_FIXTURES:
        rows.extend(load_jsonl(name))
    _submit_fixture_rows(ctx, rows)

    control_ids: list[str] = []
    for text in _CONTROL_QUESTIONS:
        created = ctx.client.create_ticket(text)
        control_ids.append(str(created["id"]))
    ctx.bindings["control_ticket_ids"] = ",".join(control_ids)

    run = ctx.client.weekly_run()
    run_id = str(run["run_id"])
    _wait_weekly_status(ctx, run_id)
    _bind_weekly_actions(ctx, run_id)
    _wait_tickets_terminal(ctx)
    _pick_edit_ticket_bindings(ctx)
    _sync_bindings_from_ctx(ctx)


def _run_feas_01(ctx: EvalContext) -> CaseResult:
    bad: list[str] = []
    for fid, tid in ctx.ticket_map.items():
        detail = ctx.client.get_ticket(tid)
        st = detail.get("status")
        if st not in TERMINAL_STATUSES:
            bad.append(f"{fid}:{st}")
            continue
        row = _ticket_row(detail)
        if row is None or row < 1 or row > 5:
            bad.append(f"{fid}:row={row}")
            continue
        if row == 5:
            searched_ok = False
            for ao in detail.get("agent_outputs") or []:
                if ao.get("agent") != "facts":
                    continue
                written = ao.get("written_output") or {}
                searched = written.get("searched") or {}
                if (
                    searched.get("schemes")
                    and searched.get("sections")
                    and searched.get("snapshots")
                ):
                    searched_ok = True
                    break
            reply = (detail.get("current_draft") or {}).get("reply_text") or ""
            if "We searched" in reply:
                searched_ok = True
            if not searched_ok:
                bad.append(f"{fid}:row5-no-coverage")
    if bad:
        return _case_fail("FEAS-01", f"{len(bad)} tickets failed", actual=bad[:20])
    return _case_pass("FEAS-01", tickets=len(ctx.ticket_map), ok=len(ctx.ticket_map))


_SCHEMES = {"large_cap", "mid_cap", "small_cap", "gold_fof", "silver_fof", "elss_tax_saver"}
_SECTIONS = {
    "Key fund facts",
    "Exit load",
    "Stamp duty and tax",
    "Minimum investments",
    "Fund management",
    "Benchmark",
    "Investment objective",
}
_THEMES = {"trading_charts", "money_movement", "reliability", "fees_brokerage", "other"}
_FLAGS = {"advice", "performance_claim", "none"}


def _choice_ok(agent: str, choice: Any) -> bool:
    """validated_choice is in the agent's allow-list or an explicit null (eval §4 FEAS-02)."""
    if choice in (None, ""):
        return True
    text = str(choice)
    if agent == "facts":
        scheme, _, section = text.partition("|")
        return scheme in _SCHEMES | {"null"} and section in _SECTIONS | {"null"}
    if agent == "pattern":
        return text in _THEMES
    if agent == "compliance":
        return text in _FLAGS
    return False


def _run_feas_02(ctx: EvalContext) -> CaseResult:
    """
    eval §4: every ticket has facts, pattern and compliance output on its first run;
    each (agent, attempt) appears once, i.e. one model call per node per run; and every
    validated choice is in its allow-list or null.
    """
    bad: list[str] = []
    for fid, tid in ctx.ticket_map.items():
        outputs = ctx.client.get_ticket(tid).get("agent_outputs") or []
        first = {str(o.get("agent")) for o in outputs if int(o.get("attempt") or 1) == 1}
        if first != set(AGENTS):
            bad.append(f"{fid}:first-run agents={sorted(first)}")
        seen: set[tuple[str, int]] = set()
        for o in outputs:
            key = (str(o.get("agent")), int(o.get("attempt") or 1))
            if key in seen:
                bad.append(f"{fid}:{key[0]} called twice in attempt {key[1]}")
            seen.add(key)
            if not _choice_ok(key[0], o.get("validated_choice")):
                bad.append(f"{fid}:{key[0]}={o.get('validated_choice')!r} not allowed")
    if bad:
        return _case_fail("FEAS-02", f"{len(bad)} agent-output problems", actual=bad[:15])
    return _case_pass("FEAS-02", tickets=len(ctx.ticket_map))


def _run_feas_03(ctx: EvalContext) -> CaseResult:
    if ctx.mode == "ci":
        return _case_skip(
            "FEAS-03",
            "graded mode only (model ERROR rate)",
        )
    total = 0
    errors = 0
    for _fid, tid in ctx.ticket_map.items():
        for ao in ctx.client.get_ticket(tid).get("agent_outputs") or []:
            total += 1
            if ao.get("model_raw_choice") == "ERROR":
                errors += 1
    if total == 0:
        return _case_fail("FEAS-03", "no agent outputs")
    rate = errors / total
    if rate > 0.10:
        return _case_fail(
            "FEAS-03",
            f"ERROR rate {rate:.1%} > 10%",
            metric={"errors": errors, "total": total},
        )
    return _case_pass("FEAS-03", errors=errors, total=total, rate=round(rate, 4))


def _review_id_set() -> set[str]:
    payload = json.loads(REVIEWS_PATH.read_text(encoding="utf-8"))
    return {str(r["id"]) for r in payload}


def _run_feas_04(ctx: EvalContext) -> CaseResult:
    run_id = ctx.weekly_run_id
    if not run_id:
        return _case_fail("FEAS-04", "no weekly_run_id")
    detail = ctx.client.get_weekly_run(run_id)
    themes = detail.get("themes") or []
    if len(themes) > 5:
        return _case_fail("FEAS-04", f"too many themes: {len(themes)}")
    known = _review_id_set()
    bad: list[str] = []
    total_reviews = 0
    for th in themes:
        rids = [str(x) for x in (th.get("review_ids") or [])]
        count = int(th.get("count") or 0)
        if len(rids) != count:
            bad.append(f"{th.get('theme_id')}:len!=count")
        total_reviews += count
        if not th.get("window_start") or not th.get("window_end"):
            bad.append(f"{th.get('theme_id')}:null-window")
        missing = [r for r in rids if r not in known]
        if missing:
            bad.append(f"{th.get('theme_id')}:unknown-review")
    if bad:
        return _case_fail("FEAS-04", "; ".join(bad[:10]), actual=bad)
    return _case_pass("FEAS-04", themes=len(themes), reviews=total_reviews)


def _within_pct(actual: int, expected: int, pct: float = 0.02) -> bool:
    if expected == 0:
        return actual == 0
    lo = expected * (1 - pct)
    hi = expected * (1 + pct)
    return lo <= actual <= hi


def _run_feas_05(ctx: EvalContext) -> CaseResult:
    run_id = ctx.weekly_run_id
    if not run_id:
        return _case_fail("FEAS-05", "no weekly_run_id")
    detail = ctx.client.get_weekly_run(run_id)
    themes = {str(t["theme_id"]): int(t["count"]) for t in (detail.get("themes") or [])}
    expected = baseline_counts()
    drifts: list[str] = []
    for theme_id in BASELINE_ORDER:
        target = int(expected.get(theme_id, 0))
        got = int(themes.get(theme_id, 0))
        if target <= 0 or not _within_pct(got, target):
            drifts.append(f"{theme_id}:{got} vs {target}")
    fallback_note = None
    try:
        with session_scope(ctx.db_url) as session:
            row = session.scalars(
                select(models.AuditEvent)
                .where(
                    models.AuditEvent.entity_id == run_id,
                    models.AuditEvent.event == "weekly_run_completed",
                )
                .order_by(models.AuditEvent.ts.desc())
            ).first()
            if row and isinstance(row.details, dict):
                if row.details.get("used_baseline_fallback"):
                    fallback_note = True
    except Exception:
        pass
    metric: dict[str, Any] = {"drifts": drifts, "used_baseline_fallback": fallback_note}
    if drifts:
        return _case_fail(
            "FEAS-05",
            f"{len(drifts)} themes out of tolerance",
            actual=drifts,
            metric=metric,
        )
    return _case_pass("FEAS-05", **metric)


def _review_window_ends() -> list[date]:
    return week_ending_as_of_values(date(2026, 4, 12), date(2026, 7, 4))


def _run_feas_06(ctx: EvalContext) -> CaseResult:
    """
    eval §4: the current run plus one run for every other week-end in the review window.
    Every pulse must be ≤ 250 words, recomputed here rather than read from the API.
    """
    run_id = ctx.weekly_run_id
    if not run_id:
        return _case_fail("FEAS-06", "no weekly_run_id")
    cur_as_of = str(ctx.client.get_weekly_run(run_id).get("as_of"))
    # Every other week-end in the review window (11 for 12-Apr → 04-Jul-2026).
    targets = [d for d in _review_window_ends() if d.isoformat() != cur_as_of]
    if len(targets) < 11:
        return _case_fail("FEAS-06", f"only {len(targets)} extra week-ends in the window")

    problems: list[str] = []
    word_counts: list[int] = []

    def _check(rid: str) -> None:
        pulse = ctx.client.get_weekly_run(rid).get("pulse")
        if not pulse:
            problems.append(f"{rid}:no pulse")
            return
        words = count_words(str(pulse.get("body_md") or ""))
        word_counts.append(words)
        if words > 250:
            problems.append(f"{rid}:words={words}")

    _check(run_id)
    for as_of in targets:
        resp = ctx.client.weekly_run(as_of=as_of)
        rid = str(resp["run_id"])
        finished = _wait_weekly_status(ctx, rid, timeout=120)
        if finished.get("status") != "awaiting_approval":
            problems.append(f"{as_of}:status={finished.get('status')}")
            continue
        _check(rid)
    if problems:
        return _case_fail("FEAS-06", f"{len(problems)} pulses failed", actual=problems[:10])
    return _case_pass("FEAS-06", checked=len(word_counts), max_words=max(word_counts))


def _run_feas_07(ctx: EvalContext) -> CaseResult:
    run_id = ctx.weekly_run_id
    if not run_id:
        return _case_fail("FEAS-07", "no weekly_run_id")
    detail = ctx.client.get_weekly_run(run_id)
    expl = detail.get("explainer")
    if not expl:
        return _case_fail("FEAS-07", "no explainer on run")
    bullets = list(expl.get("bullets") or [])
    urls = list(expl.get("source_urls") or [])
    if len(bullets) > 6:
        return _case_fail("FEAS-07", f"bullets={len(bullets)}")
    if len(set(urls)) != 2:
        return _case_fail("FEAS-07", f"distinct urls={len(set(urls))}")
    if not expl.get("last_checked"):
        return _case_fail("FEAS-07", "missing last_checked")
    body = " ".join(
        [
            str(expl.get("id") or ""),
            json.dumps(bullets, default=str),
            " ".join(urls),
        ]
    ).lower()
    if "brokerage" not in body and "scheme-level" not in body and "scheme level" not in body:
        actions = ctx.client.list_actions(status_filter="")
        for item in actions["items"]:
            if item.get("subject_type") != "fee_explainer":
                continue
            payload = str(item.get("payload") or "").lower()
            if "brokerage" in payload or "scheme-level" in payload:
                body = payload
                break
    if "brokerage" not in body and "scheme-level" not in body and "scheme level" not in body:
        return _case_fail("FEAS-07", "scope line not found in explainer text")
    return _case_pass("FEAS-07", bullets=len(bullets), urls=len(set(urls)))


def _run_feas_08(ctx: EvalContext) -> CaseResult:
    """eval §4: trigger a run, then only poll; awaiting_approval with 4 pending actions."""
    resp = ctx.client.weekly_run()
    run_id = str(resp.get("run_id") or "")
    if not run_id or resp.get("noop"):
        return _case_fail("FEAS-08", "manual trigger did not start a new run", actual=resp)
    detail = _wait_weekly_status(ctx, run_id, timeout=120)
    if detail.get("status") != "awaiting_approval":
        return _case_fail(
            "FEAS-08",
            f"status={detail.get('status')}",
            expected="awaiting_approval",
        )
    subjects = {
        str((detail.get("pulse") or {}).get("id") or ""),
        str((detail.get("explainer") or {}).get("id") or ""),
    } - {""}
    pending = ctx.client.list_actions(status_filter="pending")["items"]
    weekly_pending = [
        a
        for a in pending
        if a.get("subject_type") in {"pulse", "fee_explainer"}
        and str(a.get("subject_id")) in subjects
    ]
    if len(weekly_pending) != 4:
        return _case_fail(
            "FEAS-08",
            f"expected 4 pending weekly actions, got {len(weekly_pending)}",
        )
    return _case_pass("FEAS-08", run_id=run_id, pending_weekly=4)


def _weekly_run_thread(ctx: EvalContext, out: list[Any], err: list[Any]) -> None:
    try:
        out.append(ctx.client.weekly_run())
    except EvalHttpError as exc:
        err.append(exc)


def _run_feas_09(ctx: EvalContext) -> CaseResult:
    """
    eval §4 / arch §7.1: concurrent triggers → 409 for the loser; a cron re-trigger with
    unchanged input → no new run; a manual re-trigger → a new run that supersedes only
    the previous run's pending actions.
    """
    results: list[Any] = []
    errors: list[EvalHttpError] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_weekly_run_thread, ctx, results, errors) for _ in range(6)]
        for fut in futures:
            fut.result(timeout=300)
    unexpected = [e.status for e in errors if e.status != 409]
    if unexpected:
        return _case_fail("FEAS-09", f"unexpected statuses {unexpected}")
    if not any(e.status == 409 for e in errors):
        return _case_fail(
            "FEAS-09",
            "6 concurrent triggers produced no 409 (run lock not observed)",
            metric={"ok": len(results)},
        )
    for res in results:
        _wait_weekly_status(ctx, str(res["run_id"]), timeout=120)

    before_runs = ctx.client.list_weekly_runs(limit=200)["total"]
    cron = ctx.client.weekly_run(trigger="cron")
    if not cron.get("noop"):
        return _case_fail("FEAS-09", "cron re-trigger with same input should noop", actual=cron)
    if ctx.client.list_weekly_runs(limit=200)["total"] != before_runs:
        return _case_fail("FEAS-09", "cron noop created a weekly_runs row")

    prev_pending = [
        str(a["id"])
        for a in ctx.client.list_actions(status_filter="pending")["items"]
        if a.get("subject_type") in {"pulse", "fee_explainer"}
    ]
    decided_before = {
        str(a["id"]): a.get("status")
        for a in ctx.client.list_actions(status_filter="")["items"]
        if a.get("status") in {"executed", "rejected", "approved"}
    }
    manual = ctx.client.weekly_run()
    if manual.get("noop"):
        return _case_fail("FEAS-09", "manual re-trigger returned noop (arch §7.1)")
    _wait_weekly_status(ctx, str(manual["run_id"]), timeout=120)
    if ctx.client.list_weekly_runs(limit=200)["total"] != before_runs + 1:
        return _case_fail("FEAS-09", "manual re-trigger did not add exactly one run")
    all_actions = ctx.client.list_actions(status_filter="")["items"]
    after = {str(a["id"]): a.get("status") for a in all_actions}
    not_superseded = [aid for aid in prev_pending if after.get(aid) != "superseded"]
    touched = [aid for aid, st in decided_before.items() if after.get(aid) != st]
    if not_superseded or touched:
        return _case_fail(
            "FEAS-09",
            "manual re-run must supersede only pending weekly actions",
            metric={"not_superseded": not_superseded[:5], "decided_changed": touched[:5]},
        )
    return _case_pass(
        "FEAS-09",
        concurrent_409=sum(1 for e in errors if e.status == 409),
        cron_noop=True,
        manual_run=str(manual["run_id"]),
        superseded=len(prev_pending),
    )


def _run_feas_10(ctx: EvalContext) -> CaseResult:
    run_id = ctx.weekly_run_id
    if not run_id:
        return _case_fail("FEAS-10", "no weekly run")
    pulse = ctx.client.get_weekly_run(run_id).get("pulse") or {}
    repeats = list(pulse.get("repeat_questions") or [])
    exit_clusters = [
        r
        for r in repeats
        if "exit load" in str(r.get("representative_text", "")).lower()
        or "exit-load" in str(r.get("representative_text", "")).lower()
    ]
    if not exit_clusters:
        return _case_fail("FEAS-10", "no exit-load repeat cluster in pulse", actual=repeats)
    top = max(exit_clusters, key=lambda x: int(x.get("count") or 0))
    if int(top.get("count") or 0) < 6:
        return _case_fail(
            "FEAS-10",
            f"exit-load cluster count={top.get('count')}",
            expected="≥6",
        )
    control_fps: set[str] = set()
    for tid in ctx.bindings.get("control_ticket_ids", "").split(","):
        if not tid:
            continue
        fp = ctx.client.get_ticket(tid).get("fingerprint")
        if fp:
            control_fps.add(str(fp))
    for rep in repeats:
        if str(rep.get("fingerprint")) in control_fps and int(rep.get("count") or 0) >= 4:
            return _case_fail("FEAS-10", "control cluster appeared in pulse", actual=rep)
        text = str(rep.get("representative_text", "")).lower()
        if "benchmark" in text and int(rep.get("count") or 0) >= 4:
            return _case_fail("FEAS-10", "control benchmark cluster appeared", actual=rep)
    return _case_pass("FEAS-10", exit_load_count=int(top.get("count")), clusters=len(exit_clusters))


def _run_feas_11(ctx: EvalContext) -> CaseResult:
    """
    eval §4, Link 2: (a) fee question → row 4; (b) approve the explainer email_draft;
    (c) still row 4; (d) approve the explainer doc_append; (e) row 3, origin=canonical,
    reuse log exactly 1 entry. An ELSS exit-load question stays row 4 ("Nil.").
    Runs on its own weekly run so Suite 2's bound actions are untouched.
    """
    run = ctx.client.weekly_run()
    run_id = str(run.get("run_id") or "")
    detail = _wait_weekly_status(ctx, run_id, timeout=120)
    expl_id = str((detail.get("explainer") or {}).get("id") or "")
    mine = [
        a
        for a in ctx.client.list_actions(status_filter="pending")["items"]
        if str(a.get("subject_id")) == expl_id
    ]
    expl_email = next((a["id"] for a in mine if a.get("kind") == "email_draft"), None)
    expl_doc = next((a["id"] for a in mine if a.get("kind") == "doc_append"), None)
    if not expl_email or not expl_doc:
        return _case_fail("FEAS-11", "own run has no pending explainer actions")

    fee_text = "Why was I charged an exit load on HDFC Large Cap?"
    row_a = _ticket_row(ctx.client.get_ticket(str(ctx.client.create_ticket(fee_text)["id"])))
    ctx.client.approve_action(str(expl_email))
    row_c = _ticket_row(ctx.client.get_ticket(str(ctx.client.create_ticket(fee_text)["id"])))
    canonical_before = ctx.client.get_explainers().get("active") or {}
    ctx.client.approve_action(str(expl_doc))
    active = ctx.client.get_explainers().get("active") or {}
    if not active or active.get("approved_action_id") != expl_doc:
        return _case_fail("FEAS-11", "(d) doc_append approval did not promote the explainer")
    e_tid = str(ctx.client.create_ticket(fee_text)["id"])
    e_detail = ctx.client.get_ticket(e_tid)
    row_e = _ticket_row(e_detail)
    draft = e_detail.get("current_draft") or {}
    bullet_urls = {str(b.get("source_url")) for b in active.get("bullets") or []}
    active_after = ctx.client.get_explainers().get("active") or {}
    reuse = [r for r in active_after.get("reuse_log") or [] if r.get("ticket_id") == e_tid]
    elss = ctx.client.get_ticket(
        str(ctx.client.create_ticket("What is the exit load on HDFC ELSS Tax Saver?")["id"])
    )
    row_elss = _ticket_row(elss)
    elss_text = str((elss.get("current_draft") or {}).get("reply_text") or "")
    problems = []
    if row_a != 4:
        problems.append(f"(a) row {row_a} != 4")
    if row_c != 4:
        problems.append(f"(c) row {row_c} != 4 after email approval")
    if canonical_before.get("approved_action_id") == expl_doc:
        problems.append("(b) email approval promoted the explainer")
    if row_e != 3 or draft.get("origin") != "canonical":
        problems.append(f"(e) row {row_e}, origin {draft.get('origin')} (want 3/canonical)")
    if str(draft.get("citation_url")) not in bullet_urls:
        problems.append("(e) citation is not a bullet source_url")
    if len(active_after.get("reuse_log") or []) != 1 or len(reuse) != 1:
        problems.append(f"reuse log has {len(active_after.get('reuse_log') or [])} entries, want 1")
    if row_elss != 4 or "Nil." not in elss_text:
        problems.append(f"ELSS exit load row {row_elss} (want 4 with 'Nil.')")
    if problems:
        return _case_fail("FEAS-11", "; ".join(problems))
    return _case_pass("FEAS-11", run_id=run_id, rows=[row_a, row_c, row_e], elss_row=row_elss)


def prepare_edit_run(ctx: EvalContext) -> str:
    """Fresh manual weekly run whose 4 pending actions Suite 2 acts on (eval §5.2)."""
    resp = ctx.client.weekly_run()
    run_id = str(resp.get("run_id") or "")
    _wait_weekly_status(ctx, run_id, timeout=120)
    _bind_weekly_actions(ctx, run_id)
    return run_id


def _run_feas_12(ctx: EvalContext) -> CaseResult:
    bad: list[str] = []
    for fid, tid in ctx.ticket_map.items():
        detail = ctx.client.get_ticket(tid)
        draft = detail.get("current_draft") or {}
        reply = str(draft.get("reply_text") or "")
        if reply and count_sentences(reply) > 3:
            bad.append(f"{fid}:sentences>{3}")
        row = _ticket_row(detail)
        if row in {3, 4}:
            if not draft.get("citation_url"):
                bad.append(f"{fid}:no-citation")
            if not draft.get("last_checked"):
                bad.append(f"{fid}:no-last_checked")
    if bad:
        return _case_fail("FEAS-12", f"{len(bad)} drafts failed caps", actual=bad[:20])
    return _case_pass("FEAS-12", tickets=len(ctx.ticket_map))


def _run_feas_13(ctx: EvalContext) -> CaseResult:
    return _case_skip(
        "FEAS-13",
        "requires separate process with EVAL_FAULTS=llm_down (run_all mini-check)",
    )


_CASE_RUNNERS: dict[str, Callable[[EvalContext], CaseResult]] = {
    "FEAS-01": _run_feas_01,
    "FEAS-02": _run_feas_02,
    "FEAS-03": _run_feas_03,
    "FEAS-04": _run_feas_04,
    "FEAS-05": _run_feas_05,
    "FEAS-06": _run_feas_06,
    "FEAS-07": _run_feas_07,
    "FEAS-08": _run_feas_08,
    "FEAS-09": _run_feas_09,
    "FEAS-10": _run_feas_10,
    "FEAS-11": _run_feas_11,
    "FEAS-12": _run_feas_12,
    "FEAS-13": _run_feas_13,
}


def run_feasibility(
    ctx: EvalContext,
    *,
    case_filter: str | None = None,
) -> SuiteResult:
    """Run FEAS-01..13 (calls ``setup_feasibility`` when no weekly run yet)."""
    if not ctx.weekly_run_id:
        setup_feasibility(ctx)

    case_ids = _filter_cases(list(_CASE_RUNNERS), case_filter)
    cases: list[CaseResult] = []
    for cid in case_ids:
        runner = _CASE_RUNNERS[cid]
        try:
            cases.append(runner(ctx))
        except Exception as exc:  # noqa: BLE001 — report harness failures
            cases.append(_case_fail(cid, str(exc)))

    status: str = "fail" if any(c.status == "fail" for c in cases) else "pass"
    return SuiteResult(name="feasibility", status=status, cases=cases)  # type: ignore[arg-type]
