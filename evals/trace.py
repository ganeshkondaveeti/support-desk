"""Claim tracing for GC-01 / GC-02 (eval.md §6.2)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.corpus.loader import Corpus, load_corpus
from app.guardrails.pii import redact
from app.response.templates import SCHEME_DISPLAY

# "For the equity schemes (A, B, C): <fact>" and the canonical-reuse variant
# "For HDFC's equity schemes in this list (A, B): <fact>" (arch §8.5, §9.2).
_FAMILY_LEAD_RE = re.compile(
    r"^For (?:the |HDFC's )(?:equity|fof) schemes(?: in this list)? "
    r"\((?P<names>[^()]+)\):\s*(?P<rest>.*)$"
)
_SCHEME_BY_NAME = {name: sid for sid, name in SCHEME_DISPLAY.items()}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).casefold()


_LEAD_IN_PREFIXES = (
    "per the scheme page:",
    "from the scheme information:",
    "as per the fund document:",
)


def _strip_lead_ins(text: str) -> str:
    t = text.strip()
    lower = t.casefold()
    for prefix in _LEAD_IN_PREFIXES:
        if lower.startswith(prefix):
            return t[len(prefix) :].strip()
    return t


def _blocks_for_url(corpus: Corpus, citation_url: str) -> list[Any]:
    return [b for b in corpus.all_blocks.values() if b.source_url == citation_url]


def trace_reply_fact(
    sentence: str,
    citation_url: str,
    last_checked: str,
    corpus: Corpus | None = None,
) -> bool:
    """Sentence is a substring of the normalized block at ``citation_url``; dates match."""
    facts = corpus or load_corpus()
    blocks = _blocks_for_url(facts, citation_url)
    if not blocks:
        return False
    core = _strip_lead_ins(sentence)
    family_ids: list[str] | None = None
    family = _FAMILY_LEAD_RE.match(core)
    if family is not None:
        names = [n.strip() for n in family.group("names").split(",")]
        if not names or any(n not in _SCHEME_BY_NAME for n in names):
            return False
        family_ids = [_SCHEME_BY_NAME[n] for n in names]
        core = family.group("rest").strip()
    norm_sentence = _norm(core)
    if not norm_sentence:
        return False
    stripped = core.strip().rstrip(".")
    for block in blocks:
        if last_checked and block.last_checked != last_checked:
            continue
        if not (norm_sentence in _norm(block.text) or (stripped and stripped in block.text)):
            continue
        if family_ids is None:
            return True
        # A family answer holds only if every named scheme's text is identical (EC-RV-01).
        if _family_identical(facts, family_ids, block.section, block.text):
            return True
    return False


def _family_identical(corpus: Corpus, scheme_ids: list[str], section: str, text: str) -> bool:
    want = _norm(text).rstrip(".")
    for sid in scheme_ids:
        member = corpus.get(sid, section)
        if member is None or _norm(member.text).rstrip(".") != want:
            return False
    return True


def trace_explainer_bullet(bullet: dict[str, Any], explainer: dict[str, Any]) -> bool:
    """Bullet text is a corpus substring and its URL is one of the explainer's links."""
    text = str(bullet.get("text") or bullet.get("bullet") or "")
    url = str(bullet.get("source_url") or "")
    if not text or not url:
        return False
    urls = {str(u) for u in explainer.get("source_urls") or []}
    if url not in urls:
        return False
    corpus = load_corpus()
    blocks = _blocks_for_url(corpus, url)
    norm = _norm(text)
    for block in blocks:
        if norm in _norm(block.text) or text.strip() in block.text:
            return True
    return False


def trace_pulse_quote(
    quote: str,
    review_id: str,
    reviews_by_id: dict[str, Any],
    theme: dict[str, Any],
) -> bool:
    """Quote matches redacted review text and the review belongs to the theme."""
    review = reviews_by_id.get(review_id)
    if review is None:
        return False
    review_ids = theme.get("review_ids") or []
    if review_id not in review_ids:
        return False
    body = _review_text(review)
    # The pulse wraps quotes in quotation marks and the word cap may shorten them
    # with a trailing ellipsis (arch §7.6); the remaining text must be verbatim.
    q = quote.strip().strip('"').rstrip("…").rstrip().strip('"').strip()
    if not q:
        return False
    return q in body or _norm(q) in _norm(body)


def trace_theme_count(theme: dict[str, Any], reviews_by_id: dict[str, Any]) -> bool:
    """``count`` equals ``len(review_ids)`` and dates fall inside the stated window."""
    review_ids = list(theme.get("review_ids") or [])
    count = int(theme.get("count", len(review_ids)))
    if count != len(review_ids):
        return False
    window_start = theme.get("window_start")
    window_end = theme.get("window_end")
    if not window_start or not window_end:
        return False
    start = _as_date(window_start)
    end = _as_date(window_end)
    if start is None or end is None:
        return False
    for rid in review_ids:
        review = reviews_by_id.get(str(rid))
        if review is None:
            return False
        rdate = _review_date(review)
        if rdate is None or rdate < start or rdate > end:
            return False
    return True


def trace_repeat_count(item: dict[str, Any], tickets_with_fp: list[Any]) -> bool:
    """Pulse repeat ``count`` matches tickets sharing the fingerprint in the window."""
    want = int(item.get("count", 0))
    if want != len(tickets_with_fp):
        return False
    fp = item.get("fingerprint")
    if fp:
        for ticket in tickets_with_fp:
            if str(getattr(ticket, "fingerprint", None) or ticket.get("fingerprint")) != str(fp):
                return False
    return True


def _review_text(review: Any) -> str:
    if isinstance(review, dict):
        title = str(review.get("title") or "")
        text = str(review.get("text") or "")
    else:
        title = str(getattr(review, "title", "") or "")
        text = str(getattr(review, "text", "") or "")
    return redact(f"{title} {text}".strip()).text


def _review_date(review: Any) -> date | None:
    if isinstance(review, dict):
        raw = review.get("date")
    else:
        raw = getattr(review, "date", None)
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
