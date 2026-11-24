"""Suite 2 — Correctness of edits (eval.md §5)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.corpus.loader import load_corpus

from .client import EvalHttpError
from .fixtures_loader import load_expected_diff, load_planted_pii
from .state import match_expected, take_snapshot
from .types import CaseResult, EvalContext, SuiteResult


def _case_pass(case_id: str, **metric: Any) -> CaseResult:
    return CaseResult(id=case_id, status="pass", metric=metric)


def _case_fail(case_id: str, message: str, **kw: Any) -> CaseResult:
    return CaseResult(id=case_id, status="fail", message=message, **kw)


def _filter_cases(case_ids: list[str], case_filter: str | None) -> list[str]:
    if not case_filter:
        return case_ids
    upper = case_filter.upper()
    return [c for c in case_ids if c.upper() == upper]


def _request(
    ctx: EvalContext,
    method: str,
    path: str,
    *,
    expected: int | tuple[int, ...] = 200,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    client = ctx.client._client
    res = client.request(method, path, json=json_body)
    ok = (expected,) if isinstance(expected, int) else expected
    try:
        body: Any = res.json() if res.content else None
    except Exception:
        body = res.text
    if res.status_code not in ok:
        return res.status_code, body
    return res.status_code, body


def _detail_str(body: Any) -> str:
    if isinstance(body, dict):
        return str(body.get("detail") or body)
    return str(body)


def _edit_text_swap_leadin(current: str) -> str:
    if current.startswith("Per the scheme page:"):
        rest = current[len("Per the scheme page:") :].strip()
        return f"From the scheme information: {rest}"
    if current.startswith("From the scheme information:"):
        rest = current[len("From the scheme information:") :].strip()
        return f"Per the scheme page: {rest}"
    return f"From the scheme information: {current}"


def _require_binding(ctx: EvalContext, key: str) -> str:
    val = ctx.bindings.get(key) or getattr(ctx, f"{key.replace('-', '_')}_action_id", None)
    if not val:
        raise RuntimeError(f"missing binding {key!r}")
    return str(val)


def _run_edit_op(
    ctx: EvalContext,
    case_id: str,
    spec: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
) -> CaseResult:
    ok, msg = match_expected(before, after, spec, ctx.bindings, ctx.samples_dir)
    if not ok:
        return _case_fail(case_id, msg or "diff mismatch", expected=spec.get("id"))
    return _case_pass(case_id, diff_ok=True)


def _assert_status_and_error(
    case_id: str,
    status: int,
    body: Any,
    spec: dict[str, Any],
) -> CaseResult | None:
    want = spec.get("expect_status")
    if want is not None and status != want:
        return _case_fail(
            case_id,
            f"status {status}, want {want}",
            actual=body,
        )
    err_code = spec.get("response_error")
    if err_code:
        detail = _detail_str(body).casefold()
        code_ok = err_code.casefold() in detail
        cap_hints = err_code == "refinement_cap_reached" and (
            status == 409 or "refinement" in detail or "5 refinement" in detail
        )
        if not code_ok and not cap_hints:
            return _case_fail(case_id, f"expected error {err_code!r} in {detail!r}")
    resp = spec.get("response") or {}
    if resp.get("unchanged") and isinstance(body, dict) and not body.get("unchanged"):
        return _case_fail(case_id, "expected unchanged=true in response")
    return None


def _run_edit_01(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    t1 = _require_binding(ctx, "t1")
    detail = ctx.client.get_ticket(t1)
    old_send = detail.get("pending_reply_send_id")
    if old_send:
        ctx.bindings["superseded_action"] = str(old_send)
    draft = (detail.get("current_draft") or {}).get("reply_text") or ""
    new_text = _edit_text_swap_leadin(str(draft))
    before = take_snapshot(ctx.db_url)
    _, body = _request(
        ctx,
        "POST",
        f"/api/tickets/{t1}/edit",
        json_body={"text": new_text},
    )
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-01", spec, before, after)


def _run_edit_02(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    t1 = _require_binding(ctx, "t1")
    draft = str((ctx.client.get_ticket(t1).get("current_draft") or {}).get("reply_text") or "")
    before = take_snapshot(ctx.db_url)
    status, body = _request(
        ctx,
        "POST",
        f"/api/tickets/{t1}/edit",
        json_body={"text": draft},
    )
    err = _assert_status_and_error("EDIT-02", status, body, spec)
    if err:
        return err
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-02", spec, before, after)


def _run_edit_03(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    t1 = _require_binding(ctx, "t1")
    pii = load_planted_pii()["literals"]
    phone = next(str(x["value"]) for x in pii if x.get("type") == "PHONE")
    draft = str((ctx.client.get_ticket(t1).get("current_draft") or {}).get("reply_text") or "")
    before = take_snapshot(ctx.db_url)
    _request(
        ctx,
        "POST",
        f"/api/tickets/{t1}/edit",
        json_body={"text": f"{draft} call me {phone}"},
    )
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-03", spec, before, after)


def _run_edit_04(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    t2 = _require_binding(ctx, "t2")
    draft = str((ctx.client.get_ticket(t2).get("current_draft") or {}).get("reply_text") or "")
    candidates = [
        "Redemption within one year attracts a 1 percent exit load on units sold.",
        "Exit load on redeemed units is 1% if sold within 12 months.",
        "If you redeem within 12 months, exit load is 1% on the amount withdrawn.",
    ]
    uncited = next((c for c in candidates if c.strip() != draft.strip()), candidates[0])
    before = take_snapshot(ctx.db_url)
    _request(
        ctx,
        "POST",
        f"/api/tickets/{t2}/edit",
        json_body={"text": uncited},
    )
    after = take_snapshot(ctx.db_url)
    result = _run_edit_op(ctx, "EDIT-04", spec, before, after)
    if result.status != "pass":
        return result
    # eval §5.2: the failing version is current, and the ticket is blocked with no send.
    detail = ctx.client.get_ticket(t2)
    flags = ((detail.get("current_draft") or {}).get("reviewer_result") or {}).get("failures")
    if detail.get("status") != "blocked" or "UNCITED_CLAIM" not in (flags or []):
        return _case_fail("EDIT-04", f"status={detail.get('status')} flags={flags}")
    if detail.get("pending_reply_send_id"):
        return _case_fail("EDIT-04", "a reply_send is still pending after a failing edit")
    return result


def _run_edit_05(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    aid = _require_binding(ctx, "pulse_email")
    before = take_snapshot(ctx.db_url)
    ctx.client.approve_action(aid)
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-05", spec, before, after)


def _run_edit_06(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    """Canonical explainer promotion happens here (not in FEAS-11)."""
    aid = _require_binding(ctx, "expl_doc")
    before = take_snapshot(ctx.db_url)
    try:
        ctx.client.approve_action(aid)
    except EvalHttpError as exc:
        if exc.status == 409:
            return _case_fail(
                "EDIT-06",
                "expl_doc already approved; FEAS-11 must not approve expl_doc",
            )
        raise
    after = take_snapshot(ctx.db_url)
    result = _run_edit_op(ctx, "EDIT-06", spec, before, after)
    fee_text = "Stamp duty on my mutual fund purchase?"
    fee_tid = str(ctx.client.create_ticket(fee_text)["id"])
    detail = ctx.client.get_ticket(fee_tid)
    draft = detail.get("current_draft") or {}
    row = draft.get("row")
    origin = draft.get("origin")
    if row != 3:
        return _case_fail(
            "EDIT-06",
            f"post-approve fee question expected row 3, got {row}",
            metric={"canonical_check": False},
        )
    if origin != "canonical":
        return _case_fail(
            "EDIT-06",
            f"expected origin=canonical, got {origin!r}",
        )
    result.metric["canonical_row_3"] = True
    return result


def _run_edit_07(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    t3 = _require_binding(ctx, "t3")
    send_id = ctx.client.get_ticket(t3).get("pending_reply_send_id")
    if not send_id:
        return _case_fail("EDIT-07", "t3 has no pending_reply_send_id")
    ctx.bindings["t3_reply_send"] = str(send_id)
    before = take_snapshot(ctx.db_url)
    ctx.client.approve_action(str(send_id))
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-07", spec, before, after)


def _run_edit_08(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    aid = _require_binding(ctx, "pulse_email")
    before = take_snapshot(ctx.db_url)
    status, body = _request(ctx, "POST", f"/api/actions/{aid}/approve", expected=409)
    err = _assert_status_and_error("EDIT-08", status, body, spec)
    if err:
        return err
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-08", spec, before, after)


def _run_edit_09(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    aid = ctx.bindings.get("superseded_action")
    if not aid:
        return _case_fail("EDIT-09", "missing superseded_action from EDIT-01")
    before = take_snapshot(ctx.db_url)
    status, body = _request(ctx, "POST", f"/api/actions/{aid}/approve", expected=409)
    err = _assert_status_and_error("EDIT-09", status, body, spec)
    if err:
        return err
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-09", spec, before, after)


def _run_edit_10(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    t4 = _require_binding(ctx, "t4")
    before = take_snapshot(ctx.db_url)
    ctx.client.reject_ticket(t4)
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-10", spec, before, after)


def _run_edit_11(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    aid = _require_binding(ctx, "pulse_doc")
    before = take_snapshot(ctx.db_url)
    ctx.client.reject_action(aid)
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-11", spec, before, after)


def _run_edit_12(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    t5 = _require_binding(ctx, "t5")
    before = take_snapshot(ctx.db_url)
    _request(
        ctx,
        "POST",
        f"/api/tickets/{t5}/refine",
        json_body={"kind": "shorter", "hint": ""},
    )
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-12", spec, before, after)


def _run_edit_13(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    t5 = _require_binding(ctx, "t5")
    before = take_snapshot(ctx.db_url)
    _request(
        ctx,
        "POST",
        f"/api/tickets/{t5}/refine",
        json_body={"kind": "different_scheme", "hint": "HDFC Mid Cap"},
    )
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-13", spec, before, after)


def _run_edit_14(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    t5 = _require_binding(ctx, "t5")
    before = take_snapshot(ctx.db_url)
    _request(
        ctx,
        "POST",
        f"/api/tickets/{t5}/refine",
        json_body={"kind": "check_fee_issue", "hint": ""},
    )
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-14", spec, before, after)


def _run_edit_15(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    t5 = _require_binding(ctx, "t5")
    detail = ctx.client.get_ticket(t5)
    count = int(detail.get("refinement_count") or 0)
    while count < 5:
        _request(
            ctx,
            "POST",
            f"/api/tickets/{t5}/refine",
            json_body={"kind": "shorter", "hint": ""},
        )
        count = int(ctx.client.get_ticket(t5).get("refinement_count") or 0)
    before = take_snapshot(ctx.db_url)
    status, body = _request(
        ctx,
        "POST",
        f"/api/tickets/{t5}/refine",
        json_body={"kind": "shorter", "hint": ""},
        expected=409,
    )
    err = _assert_status_and_error("EDIT-15", status, body, spec)
    if err:
        return err
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-15", spec, before, after)


def _run_edit_16(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    blocked = ctx.client.create_ticket(
        "What is the exit load on HDFC Large Cap?",
        eval_ticket="advice_always",
    )
    blocked_id = str(blocked["id"])
    bdetail = ctx.client.get_ticket(blocked_id)
    if bdetail.get("status") != "blocked":
        return _case_fail(
            "EDIT-16",
            f"expected blocked ticket, got {bdetail.get('status')}",
        )
    ctx.blocked_ticket_id = blocked_id
    ctx.bindings["blocked_ticket"] = blocked_id
    block = load_corpus().get("large_cap", "Key fund facts")
    if block is None:
        return _case_fail("EDIT-16", "corpus block missing for large_cap Key fund facts")
    sentence = next(
        (s for s in block.text.split(". ") if "Expense ratio" in s),
        block.text.split(". ")[0],
    )
    if not sentence.endswith("."):
        sentence += "."
    draft = f"Per the scheme page: {sentence}"
    before = take_snapshot(ctx.db_url)
    _request(
        ctx,
        "POST",
        f"/api/tickets/{blocked_id}/edit",
        json_body={"text": draft},
    )
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-16", spec, before, after)


def _run_edit_17(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    prev_run = str(ctx.weekly_run_id or "")
    subjects: set[str] = set()
    if prev_run:
        detail = ctx.client.get_weekly_run(prev_run)
        subjects.add(str((detail.get("pulse") or {}).get("id") or ""))
        subjects.add(str((detail.get("explainer") or {}).get("id") or ""))
    subjects.discard("")
    pending_ids = [
        str(a["id"])
        for a in ctx.client.list_actions(status_filter="pending")["items"]
        if a.get("subject_type") in {"pulse", "fee_explainer"}
        and str(a.get("subject_id")) in subjects
    ]
    if not pending_ids:
        return _case_fail("EDIT-17", "precondition: no pending weekly action to supersede")
    before = take_snapshot(ctx.db_url)
    # A manual re-run with unchanged input still creates a new run (arch §7.1).
    resp = ctx.client.weekly_run()
    if resp.get("noop"):
        return _case_fail("EDIT-17", "manual re-run returned noop (arch §7.1)")
    after = take_snapshot(ctx.db_url)
    if pending_ids:
        for aid in pending_ids:
            after_row = next(
                (r for r in after.get("proposed_actions", []) if str(r.get("id")) == aid),
                None,
            )
            if after_row is None or after_row.get("status") != "superseded":
                return _case_fail(
                    "EDIT-17",
                    f"pending weekly action {aid} not superseded",
                )
    return _run_edit_op(ctx, "EDIT-17", spec, before, after)


def _run_edit_18(ctx: EvalContext, spec: dict[str, Any]) -> CaseResult:
    t4 = _require_binding(ctx, "t4")
    draft = str((ctx.client.get_ticket(t4).get("current_draft") or {}).get("reply_text") or "test")
    before = take_snapshot(ctx.db_url)
    status, body = _request(
        ctx,
        "POST",
        f"/api/tickets/{t4}/edit",
        json_body={"text": draft},
        expected=409,
    )
    err = _assert_status_and_error("EDIT-18", status, body, spec)
    if err:
        return err
    after = take_snapshot(ctx.db_url)
    return _run_edit_op(ctx, "EDIT-18", spec, before, after)


_EDIT_RUNNERS: dict[str, Callable[[EvalContext, dict[str, Any]], CaseResult]] = {
    "EDIT-01": _run_edit_01,
    "EDIT-02": _run_edit_02,
    "EDIT-03": _run_edit_03,
    "EDIT-04": _run_edit_04,
    "EDIT-05": _run_edit_05,
    "EDIT-06": _run_edit_06,
    "EDIT-07": _run_edit_07,
    "EDIT-08": _run_edit_08,
    "EDIT-09": _run_edit_09,
    "EDIT-10": _run_edit_10,
    "EDIT-11": _run_edit_11,
    "EDIT-12": _run_edit_12,
    "EDIT-13": _run_edit_13,
    "EDIT-14": _run_edit_14,
    "EDIT-15": _run_edit_15,
    "EDIT-16": _run_edit_16,
    "EDIT-17": _run_edit_17,
    "EDIT-18": _run_edit_18,
}


def run_edit_correctness(
    ctx: EvalContext,
    *,
    case_filter: str | None = None,
) -> SuiteResult:
    """Run EDIT-01..18 state-diff cases."""
    missing = [k for k in ("t1", "t2", "t3", "t4", "t5") if not ctx.bindings.get(k)]
    if missing:
        return SuiteResult(
            name="edit",
            status="fail",
            cases=[
                _case_fail(
                    "EDIT-setup",
                    f"missing ticket bindings: {missing} (run feasibility setup first)",
                )
            ],
        )

    case_ids = _filter_cases(list(_EDIT_RUNNERS), case_filter)
    cases: list[CaseResult] = []
    for cid in case_ids:
        try:
            spec = load_expected_diff(cid)
            runner = _EDIT_RUNNERS[cid]
            cases.append(runner(ctx, spec))
        except Exception as exc:  # noqa: BLE001
            cases.append(_case_fail(cid, str(exc)))

    status: str = "fail" if any(c.status == "fail" for c in cases) else "pass"
    return SuiteResult(name="edit", status=status, cases=cases)  # type: ignore[arg-type]
