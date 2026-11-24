"""Orchestrator: decision table, routing, disagreement, rework (architecture §8.5)."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.bridge.canonical import lookup as canonical_lookup
from app.bridge.repeats import (
    compliance_class,
    record_ticket_fingerprint,
    repeat_count,
    window_bounds,
)
from app.db import models
from app.guardrails.length import count_sentences
from app.guardrails.pii import redact
from app.response.faults import consume_uncited_once, eval_now, fault_applies
from app.response.state import ComplianceOut, FactsOut, PatternOut, TicketState
from app.response.templates import (
    ADVICE_REFUSAL,
    AMFI_EDU_URL,
    CANONICAL_LEAD,
    DISAGREEMENT_BANNER,
    FACT_LEAD_INS,
    FAMILY_LEAD,
    PERFORMANCE_REFUSAL,
    SCHEME_DISPLAY,
    not_covered_reply,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

Priority = Literal["P1", "P2", "P3"]

TEAM_BY_THEME: dict[str, str] = {
    "fees_brokerage": "Support + Product (Pricing)",
    "money_movement": "Payments Ops",
    "reliability": "Engineering",
    "trading_charts": "Product (Trading)",
    "other": "Support",
}

_FEE_KEYWORDS = re.compile(
    r"\b(?:exit\s*load|expense\s*ratio|stamp\s*duty|brokerage|charges?|fees?|"
    r"nav|aum|fund\s+size|commission)\b",
    re.I,
)


def orchestrator_node(state: TicketState) -> dict[str, Any]:
    """LangGraph node: fingerprint bridge → draft + routing from agent outputs."""
    facts = state.get("facts") or _empty_facts()
    pattern = dict(state.get("pattern") or _empty_pattern())
    compliance = state.get("compliance") or _empty_compliance()
    ticket_id = str(state.get("ticket_id") or "")
    session = _session_from_context()
    pattern = _sync_repeats(
        session,
        ticket_id=ticket_id,
        question=str(state.get("question") or ""),
        facts=facts,
        pattern=pattern,
        compliance=compliance,
    )
    merged: TicketState = {**state, "pattern": pattern}  # type: ignore[misc]
    draft = compose_draft(merged)
    return {"draft": draft, "failures": [], "pattern": pattern}


def compose_draft(state: TicketState) -> dict[str, Any]:
    """Decision table (first matching row wins) + routing + disagreement."""
    facts = state.get("facts") or _empty_facts()
    pattern = state.get("pattern") or _empty_pattern()
    compliance = state.get("compliance") or _empty_compliance()
    failures = list(state.get("failures") or [])
    refine_kind = state.get("refine_kind")
    ticket_id = str(state.get("ticket_id") or "")
    origin = "rework" if failures else ("refinement" if refine_kind else "orchestrator")
    session = _session_from_context()

    force_row: int | None = None
    force_sentences: list[str] | None = None
    if failures:
        force_row, force_sentences = _rework_directives(failures, facts)

    row, body, citation, last_checked, draft_origin, canonical_id = _decision_row(
        facts,
        pattern,
        compliance,
        force_row=force_row,
        force_sentences=force_sentences,
        session=session,
    )
    if draft_origin == "canonical":
        origin = "canonical"

    if refine_kind == "shorter":
        body = _first_sentence(body)
        origin = "refinement"

    # Eval fault injection (APP_ENV=test only).
    body = _apply_faults(body, ticket_id=ticket_id, rework=bool(failures))

    # LENGTH rework: keep first N sentences.
    if "LENGTH" in failures:
        body = _keep_sentences(body, 3)

    # PII rework: re-redact.
    if "PII" in failures:
        body = redact(body).text

    routing = build_routing_note(facts, pattern, compliance, row=row)
    disagreement, close_on_send = disagreement_for(row, pattern)

    return {
        "reply_text": body,
        "citation_url": citation,
        "last_checked": last_checked,
        "row": row,
        "origin": origin,
        "routing_note": routing,
        "disagreement": disagreement,
        "close_on_send": close_on_send,
        "edu_url": AMFI_EDU_URL if row == 1 else None,
        "canonical_id": canonical_id,
    }


def build_routing_note(
    facts: FactsOut,
    pattern: PatternOut,
    compliance: ComplianceOut,
    *,
    row: int,
) -> dict[str, Any]:
    team = _team(facts, pattern, compliance, row=row)
    priority = _priority(pattern, compliance)
    why = _why(facts, pattern, compliance, row=row)
    note: dict[str, Any] = {
        "team": team,
        "priority": priority,
        "why": why,
        "evidence": {
            "review_ids_sample": list(pattern.get("review_ids_sample") or []),
            "in_week_repeats": int(pattern.get("in_week_repeats") or 0),
            "theme_id": pattern.get("theme_id"),
            "trend": pattern.get("trend"),
            "review_window": list(pattern.get("window") or []),
            "ticket_window": list(pattern.get("repeat_window") or []),
        },
    }
    multi = list(facts.get("multi_scheme") or [])
    if len(multi) > 1:
        note["multi_scheme"] = {
            "ids": multi,
            "answered": facts.get("scheme_id"),
            "suggest_refinement": "different_scheme",
        }
    return note


def disagreement_for(
    row: int,
    pattern: PatternOut,
) -> tuple[str | None, bool]:
    """Rows 3/4 + (repeats≥5 or rising) → banner and close_on_send=False."""
    repeats = int(pattern.get("in_week_repeats") or 0)
    trend = str(pattern.get("trend") or "one_off")
    if row not in {3, 4}:
        return None, True
    if repeats < 5 and trend != "rising":
        return None, True
    banner = DISAGREEMENT_BANNER.format(repeats=repeats or "several", trend=trend)
    return banner, False


DecisionRow = tuple[int, str, str | None, str | None, str, str | None]


def _decision_row(
    facts: FactsOut,
    pattern: PatternOut,
    compliance: ComplianceOut,
    *,
    force_row: int | None,
    force_sentences: list[str] | None,
    session: Any | None = None,
) -> DecisionRow:
    if force_row == 1 or (force_row is None and compliance.get("advice")):
        return _row1(facts)
    if force_row == 2 or (force_row is None and compliance.get("performance_claim")):
        return _row2(facts)
    if force_row == 5:
        return _row5(facts)
    if force_row == 4 or force_row is None:
        # Row 3: canonical reuse when an approved fee explainer covers this ask.
        if force_row is None:
            canon = _try_canonical(facts, pattern, session=session)
            if canon is not None:
                return canon
        if facts.get("covered") and (force_sentences is not None or facts.get("answer_sentences")):
            return _row4(facts, sentences=force_sentences)
        if force_row == 4 and force_sentences is not None:
            # Rework dropped all sentences → fall through to row 5.
            if not force_sentences:
                return _row5(facts)
            return _row4(facts, sentences=force_sentences)
    return _row5(facts)


def _row1(facts: FactsOut) -> DecisionRow:
    parts = [ADVICE_REFUSAL]
    citation: str | None = AMFI_EDU_URL
    last_checked: str | None = None
    if facts.get("covered") and facts.get("answer_sentences"):
        fact = str(facts["answer_sentences"][0]).strip()
        if fact:
            parts.append(fact)
            citation = facts.get("citation_url") or AMFI_EDU_URL
            last_checked = facts.get("last_checked")
    return 1, " ".join(parts), citation, last_checked, "orchestrator", None


def _row2(facts: FactsOut) -> DecisionRow:
    citation = facts.get("citation_url") if facts.get("covered") else None
    if not citation:
        citation = AMFI_EDU_URL
    last_checked = facts.get("last_checked") if facts.get("covered") else None
    return 2, PERFORMANCE_REFUSAL, citation, last_checked, "orchestrator", None


def _try_canonical(
    facts: FactsOut,
    pattern: PatternOut,
    *,
    session: Any | None = None,
) -> DecisionRow | None:
    theme = pattern.get("theme_id")
    section = facts.get("section")
    scheme_id = facts.get("scheme_id")
    if theme != "fees_brokerage" or not section:
        return None
    hit = canonical_lookup(str(theme), section, scheme_id, session=session)
    if not hit:
        return None
    bullets = [str(b) for b in (hit.get("bullets") or []) if b][:2]
    if not bullets:
        return None
    asked = scheme_id
    cited_scheme = hit.get("scheme_id")
    family = hit.get("family")
    family_schemes = list(hit.get("family_schemes") or [])
    if asked and cited_scheme and asked != cited_scheme and family and family_schemes:
        names = ", ".join(SCHEME_DISPLAY.get(s, s) for s in family_schemes)
        lead = f"For HDFC's {family} schemes in this list ({names}):"
        body = f"{lead} {' '.join(bullets)} {CANONICAL_LEAD}"
    else:
        body = " ".join([*bullets, CANONICAL_LEAD])
    citation = hit.get("source_url") or hit.get("citation_url")
    last_checked = hit.get("last_checked")
    canonical_id = hit.get("canonical_id")
    return (
        3,
        body,
        citation if isinstance(citation, str) else None,
        last_checked if isinstance(last_checked, str) else None,
        "canonical",
        str(canonical_id) if canonical_id else None,
    )


def _row4(
    facts: FactsOut,
    *,
    sentences: list[str] | None = None,
) -> DecisionRow:
    lines = list(sentences if sentences is not None else (facts.get("answer_sentences") or []))
    lines = [s.strip() for s in lines if s and str(s).strip()][:2]
    if not lines:
        return _row5(facts)
    family = facts.get("family")
    family_schemes = list(facts.get("family_schemes") or [])
    if family and family_schemes:
        names = ", ".join(SCHEME_DISPLAY.get(s, s) for s in family_schemes)
        lead = FAMILY_LEAD.format(family=family, schemes=names)
        body = f"{lead} {' '.join(lines)}"
    else:
        body = f"{FACT_LEAD_INS[0]} {' '.join(lines)}"
    return (
        4,
        body,
        facts.get("citation_url"),
        facts.get("last_checked"),
        "orchestrator",
        None,
    )


def _row5(facts: FactsOut) -> DecisionRow:
    searched = facts.get("searched") or {}
    schemes = list(searched.get("schemes") or sorted(SCHEME_DISPLAY))
    sections = list(searched.get("sections") or [])
    snapshots = list(searched.get("snapshots") or ["A", "B"])
    body = not_covered_reply(
        schemes=schemes,
        sections=sections or None,
        snapshots=snapshots,
    )
    return 5, body, None, None, "orchestrator", None


def _sync_repeats(
    session: Any | None,
    *,
    ticket_id: str,
    question: str,
    facts: FactsOut,
    pattern: PatternOut,
    compliance: ComplianceOut,
) -> PatternOut:
    """Store the real fingerprint and refresh in_week_repeats (arch §9.1)."""
    updated = dict(pattern)
    clock = eval_now() or datetime.now(tz=UTC)
    start, end = window_bounds(clock)
    updated["repeat_window"] = [
        start.astimezone(IST).date().isoformat(),
        end.astimezone(IST).date().isoformat(),
    ]
    if session is None or not ticket_id:
        return updated  # type: ignore[return-value]
    ticket = session.get(models.Ticket, ticket_id)
    if ticket is None:
        return updated  # type: ignore[return-value]
    cls = compliance_class(
        advice=bool(compliance.get("advice")),
        performance_claim=bool(compliance.get("performance_claim")),
    )
    theme = str(pattern.get("theme_id") or "other")
    section = facts.get("section")
    fp = record_ticket_fingerprint(
        session,
        ticket,
        theme_id=theme,
        section=section if isinstance(section, str) else None,
        compliance=cls,
        question=question or ticket.question_redacted,
        now=clock,
    )
    updated["in_week_repeats"] = repeat_count(session, fp, clock)
    updated["fingerprint"] = fp
    return updated  # type: ignore[return-value]


def _session_from_context() -> Any | None:
    try:
        from app.response.ingress import current_db_session

        return current_db_session()
    except Exception:  # noqa: BLE001
        return None


def _rework_directives(
    failures: list[str],
    facts: FactsOut,
) -> tuple[int | None, list[str] | None]:
    """Map reviewer failure codes to deterministic fixes (arch §8.5)."""
    codes = set(failures)
    if codes & {"ADVICE", "BUY_SELL"}:
        return 1, None
    if "PERFORMANCE" in codes:
        return 2, None
    if "UNCITED_CLAIM" in codes:
        kept = _corpus_only_sentences(facts)
        if not kept:
            return 5, []
        return 4, kept
    return None, None


def _corpus_only_sentences(facts: FactsOut) -> list[str]:
    """Keep only verbatim corpus answer sentences (drop generated text)."""
    return [s for s in (facts.get("answer_sentences") or []) if s and str(s).strip()]


def _apply_faults(body: str, *, ticket_id: str, rework: bool) -> str:
    if consume_uncited_once(ticket_id):
        # Emit an uncited numeric sentence on the first pass only.
        return f"{body} The fund returned 18% last year."
    if fault_applies("advice_always", ticket_id=ticket_id):
        return f"{body} You may want to consider staying invested."
    _ = rework
    return body


def _team(
    facts: FactsOut,
    pattern: PatternOut,
    compliance: ComplianceOut,
    *,
    row: int,
) -> str:
    pii = bool(compliance.get("pii_detected") or compliance.get("pii_types"))
    flagged = bool(compliance.get("advice") or compliance.get("performance_claim"))
    if pii or flagged:
        base = "Compliance"
        theme_team = TEAM_BY_THEME.get(str(pattern.get("theme_id") or "other"), "Support")
        if theme_team != "Support" and theme_team != base:
            return f"{base} + {theme_team}"
        return base
    if row == 5:
        theme = str(pattern.get("theme_id") or "other")
        if theme == "fees_brokerage":
            return "Support + Product (Pricing)"
        return "Support (knowledge gap)"
    return TEAM_BY_THEME.get(str(pattern.get("theme_id") or "other"), "Support")


def _priority(pattern: PatternOut, compliance: ComplianceOut) -> Priority:
    trend = str(pattern.get("trend") or "one_off")
    repeats = int(pattern.get("in_week_repeats") or 0)
    if trend == "rising" or repeats >= 5:
        priority: Priority = "P1"
    elif trend == "recurring":
        priority = "P2"
    else:
        priority = "P3"
    pii = bool(compliance.get("pii_detected") or compliance.get("pii_types"))
    if pii:
        # Bump one level; floor at P2 (arch §8.5).
        if priority == "P3":
            priority = "P2"
        elif priority == "P2":
            priority = "P1"
    return priority


def _why(
    facts: FactsOut,
    pattern: PatternOut,
    compliance: ComplianceOut,
    *,
    row: int,
) -> str:
    theme = pattern.get("theme_id") or "other"
    count = int(pattern.get("review_count") or 0)
    window = list(pattern.get("window") or [])
    review_win = "→".join(str(x) for x in window if x) or "n/a"
    trend = pattern.get("trend") or "one_off"
    repeats = int(pattern.get("in_week_repeats") or 0)
    ticket_win = list(pattern.get("repeat_window") or [])
    ticket_label = "→".join(str(x) for x in ticket_win if x) or "n/a"
    parts = [
        f"{theme}: {count} reviews {review_win}, {trend}; "
        f"{repeats} similar questions in last 7 days of tickets ({ticket_label})."
    ]
    if compliance.get("pii_types"):
        parts.append(f"PII types redacted: {', '.join(compliance['pii_types'])}.")
    if row == 1:
        parts.append("Compliance: advice refusal.")
    elif row == 2:
        parts.append("Compliance: performance refusal.")
    elif facts.get("covered"):
        parts.append(f"Facts covered: {facts.get('scheme_id')} / {facts.get('section')}.")
    else:
        parts.append("Facts not covered.")
    return " ".join(parts)


def _first_sentence(text: str) -> str:
    from app.guardrails.length import count_sentences as _cs

    _ = _cs  # keep import used for symmetry with keep
    parts = _split_sentences(text)
    return parts[0] if parts else text


def _keep_sentences(text: str, n: int) -> str:
    parts = _split_sentences(text)
    return " ".join(parts[:n])


def _split_sentences(text: str) -> list[str]:
    """Lightweight split aligned with count_sentences protection rules."""
    if not text or not text.strip():
        return []
    # Prefer the length helper's count; split on .!? for keep/first.
    cleaned = re.sub(r"\s+", " ", text.strip())
    # Protect common abbreviations roughly as length.py does.
    protected = re.sub(r"\b(Rs|e\.g|i\.e|p\.a)\.", r"\1‹DOT›", cleaned, flags=re.I)
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", protected) if p.strip()]
    return [p.replace("‹DOT›", ".") for p in parts]


def _empty_facts() -> FactsOut:
    return {
        "scheme_id": None,
        "section": None,
        "covered": False,
        "answer_sentences": [],
        "citation_url": None,
        "snapshot": None,
        "last_checked": None,
        "searched": {"schemes": sorted(SCHEME_DISPLAY), "sections": [], "snapshots": ["A", "B"]},
    }


def _empty_pattern() -> PatternOut:
    return {
        "theme_id": "other",
        "trend": "one_off",
        "review_count": 0,
        "window": ["", ""],
        "review_ids_sample": [],
        "in_week_repeats": 0,
        "repeat_window": ["", ""],
    }


def _empty_compliance() -> ComplianceOut:
    return {
        "advice": False,
        "performance_claim": False,
        "pii_detected": False,
        "pii_types": [],
    }


# Silence unused import warning if count_sentences is only used indirectly.
_ = count_sentences
