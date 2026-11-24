"""Pattern agent: model picks theme_id; code adds trend/count/window (arch §8.4)."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bridge.repeats import fingerprint, in_week_repeats, window_bounds
from app.db import models
from app.guardrails.allowlists import FIXED_THEME_IDS, THEME_IDS
from app.llm.client import LLMClient, get_llm_client, match_allowed
from app.response.faults import eval_now
from app.response.state import PatternOut
from app.weekly.cluster import THEME_META


def _session_from_context() -> Session | None:
    try:
        from app.response.ingress import current_db_session

        return current_db_session()
    except Exception:  # noqa: BLE001
        return None


logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
PROMPT_PATH = Path(__file__).resolve().parents[2] / "llm" / "prompts" / "pattern.md"

# Section cues for a provisional fingerprint while Facts still runs in parallel.
_SECTION_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Exit load", re.compile(r"\bexit[\s\-]?loads?\b", re.I)),
    ("Stamp duty and tax", re.compile(r"\bstamp\s*duty\b|\btax(?:es|ed|ation)?\b", re.I)),
    (
        "Key fund facts",
        re.compile(
            r"\bexpense ratio\b|\briskometer\b|\baum\b|\bfund size\b|\bnav\b",
            re.I,
        ),
    ),
)

_ADVICE_CUE = re.compile(
    r"\bshould i\b|\brecommend\b|\badvice\b|\bworth (?:it|buying)\b",
    re.I,
)
_PERF_CUE = re.compile(
    r"\breturns?\b|\bcagr\b|\bhow much (?:will|did)\b|\bperformance\b",
    re.I,
)

_THEME_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "fees_brokerage",
        re.compile(
            r"\bexit[\s-]?loads?\b|\bbrokerage\b|\bfees?\b|\bcharg(?:e|es|ed)\b"
            r"|\bcommissions?\b|\bexpense ratio\b|\bstamp\s*duty\b",
            re.I,
        ),
    ),
    (
        "money_movement",
        re.compile(
            r"\bwithdr(?:aw\w*|ew)\b|\bdeposits?\b|\bpayments?\b|\brefunds?\b|\bupi\b|\bbanks?\b",
            re.I,
        ),
    ),
    (
        "trading_charts",
        re.compile(
            r"\bcharts?\b|\borders?\b|\btrades?\b|\bgtt\b|\bbuy/sell(?:\s+button)?\b",
            re.I,
        ),
    ),
    (
        "reliability",
        re.compile(
            r"\bcrash(?:es|ed|ing)?\b|\bslow\b|\blags?\b|\bfreez(?:e|es|ing|en)\b"
            r"|\bbugs?\b|\blog[\s-]?in\b",
            re.I,
        ),
    ),
)


def run_pattern(
    question: str,
    *,
    session: Session | None = None,
    llm: LLMClient | None = None,
    now: datetime | None = None,
    check_fee_issue: bool = False,
) -> tuple[PatternOut, dict[str, Any]]:
    if not isinstance(question, str):
        raise TypeError("pattern expects a str")
    client = llm or get_llm_client()
    client = LLMClient(backend=client.backend, call_label="pattern")

    allowed = sorted(THEME_IDS or FIXED_THEME_IDS)
    prompt = _render_prompt(allowed) + "\n\nQuestion:\n" + question
    if check_fee_issue:
        prompt += (
            "\n\nReviewer suggests checking against fees_brokerage. "
            "Prefer fees_brokerage when the question mentions charges, exit load, or brokerage."
        )
    result = client.choose(prompt, {"theme_id": allowed}, timeout=8.0)

    via_cues = False
    if result.validated is None and (
        result.raw == "ERROR" or result.backend == "cues_only" or result.error is not None
    ):
        theme_id = "fees_brokerage" if check_fee_issue else _cue_theme(question)
        via_cues = True
        validated = theme_id
    elif result.validated is None:
        theme_id = "fees_brokerage" if check_fee_issue else "other"
        validated = theme_id
    else:
        picked = result.validated if isinstance(result.validated, dict) else {}
        matched = match_allowed(picked.get("theme_id"), allowed)
        theme_id = matched if matched is not None else "other"
        if check_fee_issue and theme_id == "other":
            theme_id = "fees_brokerage"
        validated = theme_id

    snap = _latest_theme(session, theme_id)
    fee_snap = _latest_theme(session, "fees_brokerage") if check_fee_issue else None
    clock = now or eval_now() or datetime.now(tz=IST)
    section_guess = _cue_section(question)
    compliance_guess = (
        "advice"
        if _ADVICE_CUE.search(question)
        else ("performance" if _PERF_CUE.search(question) else "factual")
    )
    # Provisional fingerprint (Facts/Compliance may refine it in the orchestrator).
    fp = fingerprint(theme_id, section_guess, compliance_guess, question)
    repeats = in_week_repeats(
        fp,
        now=clock,
        session=session,
        theme_id=theme_id,
        section=section_guess,
        compliance=compliance_guess,
    )
    win_start, win_end = window_bounds(clock)
    repeat_window = [
        win_start.astimezone(IST).date().isoformat(),
        win_end.astimezone(IST).date().isoformat(),
    ]

    if snap is None:
        output: PatternOut = {
            "theme_id": theme_id,
            "trend": "one_off",
            "review_count": 0,
            "window": ["", ""],
            "review_ids_sample": [],
            "in_week_repeats": repeats,
            "repeat_window": repeat_window,
        }
    else:
        ids = list(snap.review_ids or [])[:5]
        output = {
            "theme_id": theme_id,
            "trend": _normalize_trend(snap.trend),
            "review_count": int(snap.count),
            "window": [snap.window_start.isoformat(), snap.window_end.isoformat()],
            "review_ids_sample": [str(i) for i in ids],
            "in_week_repeats": repeats,
            "repeat_window": repeat_window,
        }

    written: dict[str, Any] = {
        **output,
        "window": list(output["window"]),
        "repeat_window": list(output["repeat_window"]),
    }
    if fee_snap is not None and theme_id != "fees_brokerage":
        written["fees_brokerage_snapshot"] = {
            "count": int(fee_snap.count),
            "trend": fee_snap.trend,
            "window": [fee_snap.window_start.isoformat(), fee_snap.window_end.isoformat()],
        }
    metrics = {
        "agent": "pattern",
        "model_raw_choice": result.raw,
        "validated_choice": validated,
        "written_output": written,
        "latency_ms": result.latency_ms,
        "via": "cues" if via_cues else "model",
    }
    logger.info(
        "pattern theme=%s trend=%s count=%s",
        theme_id,
        output["trend"],
        output["review_count"],
    )
    return output, metrics


def _normalize_trend(trend: str) -> str:
    if trend in {"recurring", "rising", "one_off"}:
        return trend
    if trend in {"one-off", "oneoff"}:
        return "one_off"
    return "one_off"


def _cue_theme(text: str) -> str:
    for theme_id, cue in _THEME_CUES:
        if cue.search(text):
            return theme_id
    return "other"


def _cue_section(text: str) -> str | None:
    for section, cue in _SECTION_CUES:
        if cue.search(text):
            return section
    return None


def _latest_theme(session: Session | None, theme_id: str) -> models.ThemeSnapshot | None:
    if session is None:
        return None
    stmt = (
        select(models.ThemeSnapshot)
        .where(models.ThemeSnapshot.theme_id == theme_id)
        .order_by(models.ThemeSnapshot.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def _render_prompt(allowed: list[str]) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    lines = []
    for tid in allowed:
        meta = THEME_META.get(tid, {})
        name = meta.get("name", tid)
        desc = meta.get("description", "")
        lines.append(f"- {tid}: {name} — {desc}")
    return template.replace("{{theme_ids}}", "\n".join(lines))


def pattern_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node: reads only question; writes pattern + agent_runs.pattern."""
    question = state["question"]
    session = _session_from_context()
    check_fee = state.get("refine_kind") == "check_fee_issue"
    output, metrics = run_pattern(question, session=session, check_fee_issue=check_fee)
    return {"pattern": output, "agent_runs": {"pattern": metrics}}
