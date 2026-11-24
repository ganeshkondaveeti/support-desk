"""Suite 4 — Agent quality (informational, eval.md §7)."""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from collections.abc import Callable
from itertools import combinations
from pathlib import Path
from typing import Any

from app.corpus.loader import load_corpus

from .fixtures_loader import load_jsonl
from .types import CaseResult, EvalContext, SuiteResult

ROOT = Path(__file__).resolve().parent.parent
CHROMA_DIR = ROOT / "data" / "chroma_db"

AQ01_TARGET = 0.85
AQ02_TARGET = 0.80
AQ03_FALSE_REFUSAL_TARGET = 0.10
AQ05_PRECISION_TARGET = 0.9


def _case_pass(case_id: str, **metric: Any) -> CaseResult:
    return CaseResult(id=case_id, status="pass", metric=metric)


def _case_fail(case_id: str, message: str, **kw: Any) -> CaseResult:
    return CaseResult(id=case_id, status="fail", message=message, **kw)


def _case_info(case_id: str, message: str = "", **kw: Any) -> CaseResult:
    metric = kw.pop("metric", None)
    if metric is None and kw:
        metric = kw
    elif metric is None:
        metric = {}
    return CaseResult(id=case_id, status="info", message=message, metric=metric)


def _filter_cases(case_ids: list[str], case_filter: str | None) -> list[str]:
    if not case_filter:
        return case_ids
    upper = case_filter.upper()
    return [c for c in case_ids if c.upper() == upper]


def _ticket_row(detail: dict[str, Any]) -> int | None:
    draft = detail.get("current_draft") or {}
    row = draft.get("row")
    if isinstance(row, int):
        return row
    result = draft.get("reviewer_result") or {}
    raw = result.get("row")
    return int(raw) if isinstance(raw, int) else None


def _agent_output(detail: dict[str, Any], agent: str) -> dict[str, Any] | None:
    rows = [r for r in detail.get("agent_outputs") or [] if r.get("agent") == agent]
    if not rows:
        return None
    return max(rows, key=lambda r: int(r.get("attempt") or 0))


def _parse_facts_choice(detail: dict[str, Any]) -> tuple[str | None, str | None]:
    out = _agent_output(detail, "facts")
    if not out:
        return None, None
    validated = out.get("validated_choice")
    if isinstance(validated, str) and "|" in validated:
        scheme, section = validated.split("|", 1)
        scheme = scheme if scheme != "null" else None
        section = section if section != "null" else None
        return scheme, section
    written = out.get("written_output") or {}
    return written.get("scheme_id"), written.get("section")


def _parse_theme(detail: dict[str, Any]) -> str | None:
    out = _agent_output(detail, "pattern")
    if not out:
        return None
    validated = out.get("validated_choice")
    if isinstance(validated, str) and validated:
        return validated
    written = out.get("written_output") or {}
    theme = written.get("theme_id")
    return str(theme) if theme else None


def _compliance_flags(detail: dict[str, Any]) -> tuple[bool, bool]:
    out = _agent_output(detail, "compliance")
    if not out:
        return False, False
    written = out.get("written_output") or {}
    return bool(written.get("advice")), bool(written.get("performance_claim"))


def _run_aq_01(ctx: EvalContext) -> CaseResult:
    rows = load_jsonl("benign_facts.jsonl")
    ok_both = 0
    ok_scheme = 0
    ok_section = 0
    total = 0
    confusion: Counter[str] = Counter()
    missing: list[str] = []
    for row in rows:
        fid = str(row["id"])
        tid = ctx.ticket_map.get(fid)
        if not tid:
            missing.append(fid)
            continue
        gold = (row.get("expect") or {}).get("gold") or {}
        g_scheme = gold.get("scheme_id")
        g_section = gold.get("section")
        detail = ctx.client.get_ticket(tid)
        scheme, section = _parse_facts_choice(detail)
        total += 1
        scheme_hit = scheme == g_scheme
        section_hit = section == g_section
        if scheme_hit:
            ok_scheme += 1
        if section_hit:
            ok_section += 1
        if scheme_hit and section_hit:
            ok_both += 1
        else:
            confusion[f"{scheme}|{section}->{g_scheme}|{g_section}"] += 1
    if not total:
        return _case_info("AQ-01", "no DQ-9 tickets in ticket_map", value=0.0, target=AQ01_TARGET)
    accuracy = ok_both / total
    metric = {
        "value": round(accuracy, 4),
        "target": AQ01_TARGET,
        "exact_match_pct": round(100 * accuracy, 2),
        "scheme_pct": round(100 * ok_scheme / total, 2),
        "section_pct": round(100 * ok_section / total, 2),
        "n": total,
        "missing_fixtures": missing[:5],
        "confusion_top": confusion.most_common(5),
    }
    status = "pass" if accuracy >= AQ01_TARGET else "fail"
    return CaseResult(id="AQ-01", status=status, metric=metric)


def _run_aq_02(ctx: EvalContext) -> CaseResult:
    labeled: list[tuple[str, dict[str, Any]]] = []
    for name in ("m3_complaints", "benign_facts"):
        for row in load_jsonl(f"{name}.jsonl"):
            gold = (row.get("expect") or {}).get("gold") or {}
            if gold.get("theme_id"):
                labeled.append((str(row["id"]), row))
    if not labeled:
        return _case_info("AQ-02", "no gold theme_id fixtures")
    ok = 0
    total = 0
    confusion: Counter[str] = Counter()
    for fid, row in labeled:
        tid = ctx.ticket_map.get(fid)
        if not tid:
            continue
        theme = _parse_theme(ctx.client.get_ticket(tid))
        want = str(((row.get("expect") or {}).get("gold") or {}).get("theme_id"))
        total += 1
        if theme == want:
            ok += 1
        else:
            confusion[f"{theme}->{want}"] += 1
    if not total:
        return _case_info("AQ-02", "no tickets available for theme check")
    acc = ok / total
    metric = {
        "value": round(acc, 4),
        "target": AQ02_TARGET,
        "correct": ok,
        "n": total,
        "confusion_top": confusion.most_common(5),
    }
    status = "pass" if acc >= AQ02_TARGET else "fail"
    return CaseResult(id="AQ-02", status=status, metric=metric)


def _run_aq_03(ctx: EvalContext) -> CaseResult:
    advice_ids = [tid for key, tid in ctx.ticket_map.items() if key.startswith("DQ6-")]
    return_ids = [tid for key, tid in ctx.ticket_map.items() if key.startswith("DQ7-")]
    positives_advice = 0
    positives_returns = 0
    model_advice_hit = 0
    model_return_hit = 0
    for tid in advice_ids:
        detail = ctx.client.get_ticket(tid)
        if _ticket_row(detail) == 1:
            positives_advice += 1
        adv, _ = _compliance_flags(detail)
        if adv:
            model_advice_hit += 1
    for tid in return_ids:
        detail = ctx.client.get_ticket(tid)
        if _ticket_row(detail) == 2:
            positives_returns += 1
        _, perf = _compliance_flags(detail)
        if perf:
            model_return_hit += 1
    negatives_total = 0
    false_refusals = 0
    for row in load_jsonl("benign_facts.jsonl"):
        tid = ctx.ticket_map.get(str(row["id"]))
        if not tid:
            continue
        negatives_total += 1
        detail = ctx.client.get_ticket(tid)
        if _ticket_row(detail) in {1, 2}:
            false_refusals += 1
    combined_pos = positives_advice + positives_returns
    combined_den = len(advice_ids) + len(return_ids)
    combined_recall = combined_pos / combined_den if combined_den else 0.0
    false_rate = false_refusals / negatives_total if negatives_total else 0.0
    model_den = len(advice_ids) + len(return_ids)
    model_pos = model_advice_hit + model_return_hit
    model_recall = model_pos / model_den if model_den else 0.0
    metric = {
        "combined_recall": round(combined_recall, 4),
        "model_recall": round(model_recall, 4),
        "false_refusal_rate": round(false_rate, 4),
        "false_refusal_target": AQ03_FALSE_REFUSAL_TARGET,
        "positives_hit": combined_pos,
        "positives_total": combined_den,
        "false_refusals": false_refusals,
        "negatives_total": negatives_total,
    }
    # Informational targets: combined 100%, false refusals <= 10%.
    combined_ok = combined_recall >= 0.999
    false_ok = false_rate <= AQ03_FALSE_REFUSAL_TARGET
    status = "pass" if combined_ok and false_ok else "fail"
    msg = ""
    if not combined_ok:
        msg = f"combined recall {combined_recall:.2%}"
    elif not false_ok:
        msg = f"false refusal rate {false_rate:.2%}"
    return CaseResult(id="AQ-03", status=status, message=msg, metric=metric)


def _scheme_id_from_name(name: str) -> str | None:
    corpus = load_corpus()
    needle = name.casefold().strip()
    for sid, display in corpus.scheme_names.items():
        if display.casefold() == needle or needle in display.casefold():
            return sid
    return None


def _chroma_top1(question: str) -> tuple[str | None, str | None]:
    import chromadb  # type: ignore[import-not-found]

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    coll = client.get_collection("mf_facts")
    res = coll.query(query_texts=[question], n_results=1)
    metas = (res.get("metadatas") or [[]])[0]
    if not metas:
        return None, None
    meta = metas[0] or {}
    scheme_name = str(meta.get("scheme_name") or "")
    section = str(meta.get("section") or "") or None
    return _scheme_id_from_name(scheme_name), section


def _run_aq_04(ctx: EvalContext) -> CaseResult:
    if not os.environ.get("GEMINI_API_KEY"):
        return _case_info(
            "AQ-04",
            "skipped without GEMINI_API_KEY",
            skipped=True,
        )
    if not CHROMA_DIR.is_dir():
        return _case_info("AQ-04", f"missing chroma dir: {CHROMA_DIR}", skipped=True)
    rows = [r for r in load_jsonl("benign_facts.jsonl") if (r.get("expect") or {}).get("row") == 4]
    agreed = 0
    total = 0
    errors: list[str] = []
    try:
        for row in rows:
            fid = str(row["id"])
            tid = ctx.ticket_map.get(fid)
            if not tid:
                continue
            detail = ctx.client.get_ticket(tid)
            question = str(detail.get("question_redacted") or row.get("text") or "")
            facts_scheme, facts_section = _parse_facts_choice(detail)
            chroma_scheme, chroma_section = _chroma_top1(question)
            total += 1
            if facts_scheme == chroma_scheme and facts_section == chroma_section:
                agreed += 1
    except Exception as exc:  # noqa: BLE001
        return _case_info("AQ-04", f"chroma query failed: {exc}", skipped=True)
    if not total:
        return _case_info("AQ-04", "no covered tickets for chroma cross-check")
    rate = agreed / total
    return _case_info(
        "AQ-04",
        metric={
            "value": round(rate, 4),
            "agreed": agreed,
            "n": total,
            "errors": errors,
        },
    )


def _run_aq_05(ctx: EvalContext) -> CaseResult:
    dq2: list[tuple[str, str]] = []
    dq3: list[tuple[str, str]] = []
    for fid, tid in ctx.ticket_map.items():
        if fid.startswith("DQ2-"):
            fp = ctx.client.get_ticket(tid).get("fingerprint")
            if fp:
                dq2.append((fid, str(fp)))
        elif fid.startswith("DQ3-"):
            fp = ctx.client.get_ticket(tid).get("fingerprint")
            if fp:
                dq3.append((fid, str(fp)))
    if len(dq2) < 2:
        return _case_info("AQ-05", "insufficient DQ-2 tickets", recall=None)
    dq2_fps = {fp for _, fp in dq2}
    recall = 1.0 if len(dq2_fps) == 1 else 0.0
    tp = 0
    fp_pairs = 0
    all_items = dq2 + dq3
    by_fp: dict[str, list[str]] = defaultdict(list)
    for fid, fp in all_items:
        by_fp[fp].append(fid)
    for _fp, members in by_fp.items():
        if len(members) < 2:
            continue
        dq2_member = any(m.startswith("DQ2-") for m in members)
        if dq2_member and all(m.startswith("DQ2-") for m in members):
            tp += len(list(combinations(members, 2)))
        else:
            fp_pairs += len(list(combinations(members, 2)))
    precision = tp / (tp + fp_pairs) if (tp + fp_pairs) else 1.0
    dq3_unique = len({fp for _, fp in dq3})
    collision_rate = 1 - (dq3_unique / len(dq3)) if dq3 else 0.0
    metric = {
        "dq2_recall": recall,
        "precision": round(precision, 4),
        "precision_target": AQ05_PRECISION_TARGET,
        "dq3_collision_rate": round(collision_rate, 4),
        "dq2_n": len(dq2),
        "dq3_n": len(dq3),
    }
    status = "pass" if recall >= 1.0 and precision >= AQ05_PRECISION_TARGET else "fail"
    return CaseResult(id="AQ-05", status=status, metric=metric)


def _percentile(values: list[int], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return float(ordered[f])
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def _run_aq_06(ctx: EvalContext) -> CaseResult:
    latencies: list[int] = []
    for _fid, tid in ctx.ticket_map.items():
        detail = ctx.client.get_ticket(tid)
        ticket_latencies = [
            int(row["latency_ms"])
            for row in detail.get("agent_outputs") or []
            if row.get("latency_ms") is not None
        ]
        if ticket_latencies:
            latencies.append(max(ticket_latencies))
    if not latencies:
        return _case_info("AQ-06", "latency_ms not available on agent_outputs", n=0)
    p50 = _percentile(latencies, 0.5)
    p95 = _percentile(latencies, 0.95)
    return _case_info(
        "AQ-06",
        metric={
            "ticket_p50_ms": p50,
            "ticket_p95_ms": p95,
            "n": len(latencies),
            "weekly_duration_s": None,
        },
    )


_AQ_RUNNERS: dict[str, Callable[[EvalContext], CaseResult]] = {
    "AQ-01": _run_aq_01,
    "AQ-02": _run_aq_02,
    "AQ-03": _run_aq_03,
    "AQ-04": _run_aq_04,
    "AQ-05": _run_aq_05,
    "AQ-06": _run_aq_06,
}


def run_agent_quality(
    ctx: EvalContext,
    *,
    case_filter: str | None = None,
) -> SuiteResult:
    """Run AQ-01..06 (informational suite; suite status is always ``info``)."""
    case_ids = _filter_cases(list(_AQ_RUNNERS), case_filter)
    cases: list[CaseResult] = []
    for cid in case_ids:
        try:
            cases.append(_AQ_RUNNERS[cid](ctx))
        except Exception as exc:  # noqa: BLE001
            cases.append(_case_fail(cid, str(exc)))
    return SuiteResult(name="quality", status="info", cases=cases)
