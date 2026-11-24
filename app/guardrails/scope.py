"""Advice / buy-sell / performance validators (architecture §6.2, G3)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

Provenance = Literal["corpus", "template", "question", "generated"]


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class ExemptSpan:
    """A span that skips advice / buy-sell / performance checks (never PII)."""

    start: int
    end: int
    provenance: Provenance


_ADVICE = re.compile(
    r"\b("
    r"you (?:should|may want to|might want to|could consider)"
    r"|we recommend"
    r"|i(?:'d| would) (?:suggest|recommend)"
    r"|consider (?:staying|holding|switching|investing)"
    r"|better (?:fund|option|choice)"
    r"|good (?:fund|investment|time to)"
    r"|stay invested"
    r"|best fund"
    r"|should i (?:redeem|buy|sell|hold|invest|exit|switch|stay)"
    r"|is (?:this|that|the) (?:scheme|fund)\b[^.?!]{0,40}?\b(?:good|bad|safe|risky|worth|better)"
    r"|which fund (?:is better|should i|is best)"
    r"|worth (?:it|investing|buying|holding)"
    r")\b",
    re.IGNORECASE,
)

_BUY_SELL = re.compile(
    r"\b("
    r"(?:you (?:should|must|ought to|need to|can) )?"
    r"(?:buy|sell|redeem now|exit now|switch to|book profit|hold on to|accumulate)"
    r"|go ahead and (?:buy|sell|redeem|exit)"
    r")\b",
    re.IGNORECASE,
)

_PERFORMANCE = re.compile(
    r"("
    r"\b\d+(?:\.\d+)?\s?%\s*(?:returns?|cagr|xirr|p\.?a\.?|annuali[sz]ed|gain)\b"
    r"|\b(?:returned|delivered|generated|outperform\w*|beat the (?:market|benchmark))\b"
    r"|\b(?:1|3|5|10)[- ]?(?:y|yr|year)s? returns?\b"
    r"|\bexpected return\b"
    r"|\bwill grow\b"
    r"|\bwhat returns? should i expect\b"
    r")",
    re.IGNORECASE,
)


def mark_spans(
    text: str,
    snippets: Iterable[str],
    provenance: Provenance,
) -> list[ExemptSpan]:
    """Mark every verbatim occurrence of each non-empty snippet."""
    spans: list[ExemptSpan] = []
    for snippet in snippets:
        if not snippet:
            continue
        start = 0
        while True:
            idx = text.find(snippet, start)
            if idx < 0:
                break
            spans.append(ExemptSpan(idx, idx + len(snippet), provenance))
            start = idx + max(len(snippet), 1)
    return _merge(spans)


def mark_corpus_spans(
    text: str,
    corpus: Iterable[str],
    explainer: Iterable[str] = (),
) -> list[ExemptSpan]:
    """Mark spans that are verbatim substrings of corpus blocks or explainer bullets."""
    return mark_spans(text, list(corpus) + list(explainer), "corpus")


def check_advice(
    text: str,
    exempt: Sequence[ExemptSpan] | None = None,
) -> list[Violation]:
    return _find(_ADVICE, "ADVICE", text, exempt)


def check_buy_sell(
    text: str,
    exempt: Sequence[ExemptSpan] | None = None,
) -> list[Violation]:
    return _find(_BUY_SELL, "BUY_SELL", text, exempt)


def check_performance(
    text: str,
    exempt: Sequence[ExemptSpan] | None = None,
) -> list[Violation]:
    return _find(_PERFORMANCE, "PERFORMANCE", text, exempt)


def _find(
    pattern: re.Pattern[str],
    code: str,
    text: str,
    exempt: Sequence[ExemptSpan] | None,
) -> list[Violation]:
    hits: list[Violation] = []
    for match in pattern.finditer(text):
        span = (match.start(), match.end())
        if _covered(span, exempt):
            continue
        hits.append(Violation(code=code, span=span))
    return hits


def _covered(span: tuple[int, int], exempt: Sequence[ExemptSpan] | None) -> bool:
    if not exempt:
        return False
    start, end = span
    for item in exempt:
        if item.start <= start and end <= item.end:
            return True
    return False


def _merge(spans: list[ExemptSpan]) -> list[ExemptSpan]:
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: (s.start, s.end))
    merged: list[ExemptSpan] = [ordered[0]]
    for span in ordered[1:]:
        prev = merged[-1]
        if span.start <= prev.end and span.provenance == prev.provenance:
            merged[-1] = ExemptSpan(prev.start, max(prev.end, span.end), prev.provenance)
        else:
            merged.append(span)
    return merged
