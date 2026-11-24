"""Sentence / word / bullet counters (architecture §6.4)."""

from __future__ import annotations

import re
from collections.abc import Callable

_Repl = str | Callable[[re.Match[str]], str]


def _dot_money(match: re.Match[str]) -> str:
    return match.group(1).replace(".", "‹DOT›")


# Protect these periods so they don't end a sentence.
_PROTECT: list[tuple[re.Pattern[str], _Repl]] = [
    (re.compile(r"\b(Rs|rs|Mr|Mrs|Ms|Dr|Prof|vs|etc|approx)\.", re.I), r"\1‹DOT›"),
    (re.compile(r"\b(e\.g|i\.e|p\.a|a\.m|p\.m)\.", re.I), r"\1‹DOT›"),
    # Currency / decimals: ₹1,000.50 or 0.005 or 1243.32 — keep the internal dot.
    (re.compile(r"(₹?\d{1,3}(?:,\d{2,3})*\.\d+)"), _dot_money),
    (re.compile(r"(₹?\d+\.\d+)"), _dot_money),
]

_REVIEW_ID_TAG = re.compile(r"\[?#?r[_-]?\d+\]?", re.IGNORECASE)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_EMPHASIS = re.compile(r"[*_`~]+")
_MD_HEADING = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_BULLET_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S", re.MULTILINE)


def count_sentences(text: str) -> int:
    """Deterministic splitter that keeps Rs. / e.g. / p.a. / ₹1,000.50 intact."""
    if not text or not text.strip():
        return 0
    cleaned = re.sub(r"\s+", " ", text.strip())
    protected = cleaned
    for pattern, repl in _PROTECT:
        protected = pattern.sub(repl, protected)
    # Abbreviation period that also ends a sentence: "p.a. See" → break before capital.
    protected = re.sub(r"‹DOT›(\s+)([A-Z])", r"‹DOT›.\1\2", protected)
    parts = [p for p in re.split(r"(?<=[.!?])\s+", protected) if p.strip()]
    return len(parts)


def count_words(md: str) -> int:
    """Strip Markdown, drop review-id tags, count whitespace-delimited tokens."""
    if not md or not md.strip():
        return 0
    text = _MD_LINK.sub(r"\1", md)
    text = _MD_HEADING.sub("", text)
    text = _MD_EMPHASIS.sub("", text)
    text = _REVIEW_ID_TAG.sub(" ", text)
    text = re.sub(r"[#>[\]()]", " ", text)
    tokens = [tok for tok in text.split() if tok]
    return len(tokens)


def count_bullets(items: list[str] | str) -> int:
    """Count explainer bullets. Accepts a list or a Markdown bullet block."""
    if isinstance(items, list):
        return sum(1 for item in items if str(item).strip())
    return len(_BULLET_LINE.findall(items))
