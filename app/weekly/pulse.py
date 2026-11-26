"""Weekly pulse writer: quotes, fee line, action ideas, ≤250 words (arch §7.6)."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.guardrails.allowlists import is_public_url
from app.guardrails.length import count_words
from app.guardrails.pii import scan
from app.guardrails.scope import check_advice, check_buy_sell, check_performance
from app.weekly.cluster import Theme, lexicon_hit_density, review_text
from app.weekly.ingest import Review

IST = ZoneInfo("Asia/Kolkata")

FEE_TERM_CANDIDATES: tuple[str, ...] = (
    "charges",
    "brokerage",
    "charge",
    "hidden",
    "fee",
    "fees",
    "gst",
    "commission",
    "deducted",
    "stamp duty",
    "exit load",
    "dp charge",
)

ACTION_FALLBACKS: dict[str, str] = {
    "trading_charts": ("Clarify order-status messaging on charts when execution is delayed."),
    "money_movement": ("Surface deposit and withdrawal ETA on the payments confirmation screen."),
    "reliability": ("Add an in-app status banner when login or OTP delivery is degraded."),
    "fees_brokerage": (
        "Add an in-app exit-load explainer on the redemption screen linking the scheme page."
    ),
    "other": ("Tag uncategorized feedback for a weekly product triage queue."),
}

_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)


@dataclass(slots=True)
class PulseQuote:
    review_id: str
    text: str
    theme_id: str


@dataclass(slots=True)
class PulseDocument:
    body_md: str
    word_count: int
    quotes: list[dict[str, str]]
    action_ideas: list[str]
    repeat_questions: list[dict[str, Any]]
    degraded: bool = False
    fee_terms: list[str] = field(default_factory=list)


ActionIdeasFn = Callable[[list[Theme], list[str], list[dict[str, Any]]], list[str] | None]


def build_pulse(
    *,
    as_of: date,
    window_start: date,
    window_end: date,
    top3: list[Theme],
    fee_theme: Theme,
    reviews_by_id: dict[str, Review],
    now: datetime | None = None,
    repeat_questions: list[dict[str, Any]] | None = None,
    action_ideas_fn: ActionIdeasFn | None = None,
) -> PulseDocument:
    now_dt = now or datetime.now(tz=IST)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=IST)
    now_dt = now_dt.astimezone(IST)
    ticket_end = now_dt.date()
    ticket_start = ticket_end - timedelta(days=7)

    repeats = list(repeat_questions or [])
    fee_terms = top_fee_terms(fee_theme, reviews_by_id)

    quotes: list[PulseQuote] = []
    for theme in top3:
        quote = select_quote(theme, reviews_by_id)
        if quote is not None:
            quotes.append(quote)

    ideas, degraded = resolve_action_ideas(
        top3=top3,
        fee_terms=fee_terms,
        repeats=repeats,
        action_ideas_fn=action_ideas_fn,
    )

    body = render_pulse(
        as_of=as_of,
        window_start=window_start,
        window_end=window_end,
        ticket_start=ticket_start,
        ticket_end=ticket_end,
        top3=top3,
        fee_theme=fee_theme,
        fee_terms=fee_terms,
        quotes=quotes,
        repeats=repeats,
        ideas=ideas,
    )
    body, quotes, repeats, ideas = trim_to_word_cap(
        body,
        as_of=as_of,
        window_start=window_start,
        window_end=window_end,
        ticket_start=ticket_start,
        ticket_end=ticket_end,
        top3=top3,
        fee_theme=fee_theme,
        fee_terms=fee_terms,
        quotes=quotes,
        repeats=repeats,
        ideas=ideas,
    )
    word_count = count_words(body)
    if word_count > 250:
        raise AssertionError(f"pulse word_count {word_count} exceeds 250")

    return PulseDocument(
        body_md=body,
        word_count=word_count,
        quotes=[{"review_id": q.review_id, "text": q.text} for q in quotes],
        action_ideas=ideas,
        repeat_questions=repeats,
        degraded=degraded,
        fee_terms=fee_terms,
    )


def top_fee_terms(
    fee_theme: Theme,
    reviews_by_id: dict[str, Review],
    *,
    limit: int = 5,
) -> list[str]:
    counter: Counter[str] = Counter()
    for rid in fee_theme.review_ids:
        review = reviews_by_id.get(rid)
        if review is None:
            continue
        text_l = review_text(review).lower()
        for term in FEE_TERM_CANDIDATES:
            if term in text_l:
                if term in {"fee", "fees"}:
                    key = "fees"
                elif term in {"charge", "charges"}:
                    key = "charges"
                else:
                    key = term
                counter[key] += 1
    return [term for term, _ in counter.most_common(limit)]


def select_quote(
    theme: Theme,
    reviews_by_id: dict[str, Review],
) -> PulseQuote | None:
    candidates = _quote_candidates(theme, reviews_by_id, enforce_length=True)
    if not candidates:
        candidates = _quote_candidates(theme, reviews_by_id, enforce_length=False)
    if not candidates:
        return None
    review, text, _density = candidates[0]
    return PulseQuote(review_id=review.id, text=text, theme_id=theme.id)


def _quote_candidates(
    theme: Theme,
    reviews_by_id: dict[str, Review],
    *,
    enforce_length: bool,
) -> list[tuple[Review, str, int]]:
    out: list[tuple[Review, str, int]] = []
    for rid in theme.review_ids:
        review = reviews_by_id.get(rid)
        if review is None:
            continue
        if review.rating > 2:
            continue
        # Exclude any review where PII was found (title or text) — G2 / arch §7.6.
        if review.pii_types:
            continue
        text = review.text.strip()
        if not text:
            continue
        if enforce_length and not (40 <= len(text) <= 200):
            continue
        if check_advice(text) or check_buy_sell(text) or check_performance(text):
            continue
        density = lexicon_hit_density(review_text(review), theme.id)
        out.append((review, text, density))
    out.sort(key=lambda item: (-item[2], item[0].rating, item[0].id))
    return out


def resolve_action_ideas(
    *,
    top3: list[Theme],
    fee_terms: list[str],
    repeats: list[dict[str, Any]],
    action_ideas_fn: ActionIdeasFn | None,
) -> tuple[list[str], bool]:
    """Return ``(ideas, degraded)``. Any LLM miss uses templated fallback."""
    if action_ideas_fn is None:
        return fallback_action_ideas(top3), True
    try:
        ideas = action_ideas_fn(top3, fee_terms, repeats)
    except Exception:  # noqa: BLE001 — any LLM failure → fallback
        return fallback_action_ideas(top3), True
    if ideas is None:
        return fallback_action_ideas(top3), True
    validated = [idea for idea in ideas if validate_action_idea(idea)]
    if len(validated) == 3:
        return validated, False
    return fallback_action_ideas(top3), True


def fallback_action_ideas(top3: list[Theme]) -> list[str]:
    ideas: list[str] = []
    for theme in top3[:3]:
        ideas.append(ACTION_FALLBACKS.get(theme.id, ACTION_FALLBACKS["other"]))
    while len(ideas) < 3:
        ideas.append(ACTION_FALLBACKS["other"])
    return ideas[:3]


def validate_action_idea(idea: str) -> bool:
    if not idea or not idea.strip():
        return False
    if len(idea.split()) > 25:
        return False
    if scan(idea):
        return False
    if check_advice(idea) or check_buy_sell(idea) or check_performance(idea):
        return False
    for url in _URL_RE.findall(idea):
        if not is_public_url(url):
            return False
    return True


def render_pulse(
    *,
    as_of: date,
    window_start: date,
    window_end: date,
    ticket_start: date,
    ticket_end: date,
    top3: list[Theme],
    fee_theme: Theme,
    fee_terms: list[str],
    quotes: list[PulseQuote],
    repeats: list[dict[str, Any]],
    ideas: list[str],
) -> str:
    quote_by_theme = {q.theme_id: q for q in quotes}
    lines: list[str] = [
        (
            f"# Weekly Product Pulse — week ending {as_of.isoformat()}  "
            f"(reviews {window_start.isoformat()} → {window_end.isoformat()})"
        ),
        "Facts-only. No investment advice.",
        "",
        "## Top themes",
    ]
    for idx, theme in enumerate(top3, start=1):
        lines.append(
            f"{idx}. {theme.name} — {theme.count} reviews, avg {theme.avg_rating}★, "
            f"{theme.trend} ({theme.window_start.isoformat()} → {theme.window_end.isoformat()})"
        )
        quote = quote_by_theme.get(theme.id)
        if quote:
            lines.append(f"   > {_quote_md(quote.text)} — review {quote.review_id}")
        else:
            lines.append("   > no quotable review this week")
    terms = ", ".join(fee_terms) if fee_terms else "charges, brokerage"
    lines.extend(
        [
            "",
            "## Fee confusion",
            (
                f"{fee_theme.count} reviews "
                f"({fee_theme.window_start.isoformat()} → {fee_theme.window_end.isoformat()}), "
                f"{fee_theme.trend}. Most cited: {terms}."
            ),
            (
                "Scheme-level charges (exit load, stamp duty, expense ratio) → see fee explainer; "
                "brokerage is outside the scheme corpus."
            ),
            "",
            (
                f"## Repeat questions from support "
                f"(tickets, {ticket_start.isoformat()} → {ticket_end.isoformat()})"
            ),
        ]
    )
    if repeats:
        for item in repeats:
            text = str(item.get("representative_text", ""))
            count = item.get("count", 0)
            window = item.get("window") or (
                f"{item.get('window_start', '')} → {item.get('window_end', '')}"
            )
            burst = " [possible duplicate burst]" if item.get("possible_duplicate_burst") else ""
            lines.append(f'- "{text}" — {count}× ({window}){burst}')
    else:
        lines.append("- none this ticket window")
    lines.extend(["", "## Action ideas"])
    for idx, idea in enumerate(ideas, start=1):
        lines.append(f"{idx}. {idea}")
    return "\n".join(lines).strip() + "\n"


def trim_to_word_cap(
    body: str,
    *,
    as_of: date,
    window_start: date,
    window_end: date,
    ticket_start: date,
    ticket_end: date,
    top3: list[Theme],
    fee_theme: Theme,
    fee_terms: list[str],
    quotes: list[PulseQuote],
    repeats: list[dict[str, Any]],
    ideas: list[str],
) -> tuple[str, list[PulseQuote], list[dict[str, Any]], list[str]]:
    """Trim ladder from arch §7.6 until ≤ 250 words."""
    q = list(quotes)
    r = list(repeats)
    i = list(ideas)

    def _render() -> str:
        return render_pulse(
            as_of=as_of,
            window_start=window_start,
            window_end=window_end,
            ticket_start=ticket_start,
            ticket_end=ticket_end,
            top3=top3,
            fee_theme=fee_theme,
            fee_terms=fee_terms,
            quotes=q,
            repeats=r,
            ideas=i,
        )

    body = _render()
    if count_words(body) <= 250:
        return body, q, r, i

    # 1. Shorten quotes to ≤ 20 words.
    q = [
        PulseQuote(review_id=x.review_id, text=_shorten(x.text, 20), theme_id=x.theme_id) for x in q
    ]
    body = _render()
    if count_words(body) <= 250:
        return body, q, r, i

    # 2. Drop the third quote.
    if len(q) >= 3:
        q = q[:2]
        body = _render()
        if count_words(body) <= 250:
            return body, q, r, i

    # 3. Keep only top 3 repeat questions.
    if len(r) > 3:
        extra = len(r) - 3
        r = r[:3] + [{"representative_text": f"+{extra} more", "count": extra, "window": "ui"}]
        body = _render()
        if count_words(body) <= 250:
            return body, q, r, i

    # 4. Compress action ideas to first clause.
    i = [_first_clause(idea) for idea in i]
    body = _render()
    return body, q, r, i


def _quote_md(text: str) -> str:
    """Render a review quote; avoid double-wrapping when the text already has quotes."""
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] in "\"'" and stripped[-1] == stripped[0]:
        return stripped
    return f'"{stripped}"'


def _shorten(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "…"


def _first_clause(text: str) -> str:
    for sep in (". ", "; ", " — ", " - "):
        if sep in text:
            return text.split(sep, 1)[0].strip()
    return _shorten(text, 12)


# Three ideas of ≤ 25 words plus low-effort reasoning fit well inside this.
_IDEAS_MAX_TOKENS = 384


def default_llm_action_ideas(
    top3: list[Theme],
    fee_terms: list[str],
    repeats: list[dict[str, Any]],
) -> list[str] | None:
    """
    Optional LLM path. Returns None to force the templated fallback when no
    provider is configured (P5 lands the full client).
    """
    from app.config import get_settings

    settings = get_settings()
    # Eval fault: advice_ideas injects advisory text so the pulse falls back (GC-07).
    try:
        from app.response.faults import fault_applies

        if fault_applies("advice_ideas"):
            return [
                "You should redeem now to lock gains.",
                "This fund is a good investment for everyone.",
                "Consider staying invested for higher returns.",
            ]
        # llm_down covers every model call, not only the agents' llm.choose (FEAS-13):
        # the pulse must fall back to templated ideas and mark the run degraded.
        if fault_applies("llm_down"):
            return None
    except Exception:  # noqa: BLE001
        pass
    if settings.llm_provider == "cues_only" or not (settings.groq_api_key or settings.llm_api_key):
        return None
    try:
        from groq import Groq
    except ImportError:
        return None
    client = Groq(api_key=settings.groq_api_key or settings.llm_api_key, max_retries=0)
    payload = {
        "themes": [
            {
                "id": t.id,
                "name": t.name,
                "count": t.count,
                "trend": t.trend,
                "description": t.description,
            }
            for t in top3
        ],
        "fee_terms": fee_terms,
        "repeat_questions": repeats,
    }
    prompt = (
        "Propose exactly 3 product action ideas for a mutual-fund support desk. "
        "Facts-only. No investment advice. Each idea ≤ 25 words. "
        'Return JSON {"ideas": [str, str, str]}.\n'
        f"Context: {json.dumps(payload)}"
    )
    # Same pacing as the agents (app/llm/budget.py); over budget → templated ideas.
    from app.llm.budget import estimate_tokens, get_limiter

    limiter = get_limiter()
    reservation = limiter.acquire(
        estimate_tokens(prompt, _IDEAS_MAX_TOKENS), max_wait=settings.llm_max_wait_s
    )
    if reservation is None:
        return None
    try:
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
            max_completion_tokens=_IDEAS_MAX_TOKENS,
            reasoning_effort="low",
            timeout=20.0,
        )
    except Exception:
        limiter.settle(reservation, 0)
        raise
    limiter.settle(reservation, getattr(getattr(response, "usage", None), "total_tokens", None))
    content = response.choices[0].message.content or ""
    data = json.loads(content)
    ideas = data.get("ideas")
    if not isinstance(ideas, list) or len(ideas) != 3:
        return None
    return [str(x) for x in ideas]
