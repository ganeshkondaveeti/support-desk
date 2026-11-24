"""Reviewer: hard-fail checks on drafts (architecture §8.6). Pure code, no model."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from app.corpus.loader import load_corpus
from app.guardrails.allowlists import CITATION_URLS, EDU_URLS, PUBLIC_DOMAINS, is_public_url
from app.guardrails.length import count_sentences
from app.guardrails.pii import scan
from app.guardrails.scope import (
    check_advice,
    check_buy_sell,
    check_performance,
    mark_corpus_spans,
    mark_spans,
)
from app.response.state import TicketState
from app.response.templates import (
    FACT_LEAD_INS,
    SCHEME_DISPLAY,
    TEMPLATE_SENTENCES,
    all_template_sentences,
    is_not_covered_search_sentence,
)

logger = logging.getLogger(__name__)

HARD_FAILS: tuple[str, ...] = (
    "UNCITED_CLAIM",
    "ADVICE",
    "BUY_SELL",
    "PERFORMANCE",
    "PII",
    "LENGTH",
    "URL_SCOPE",
)

_FEE_KEYWORD = re.compile(
    r"\b(?:exit\s*load|expense\s*ratio|stamp\s*duty|brokerage|charges?|fees?|"
    r"nav|aum|fund\s+size|commission|taxed\s+at)\b",
    re.I,
)
_FACTUAL_MARKER = re.compile(r"\d|%|₹")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def reviewer_node(state: TicketState) -> dict[str, Any]:
    """LangGraph node: run hard/soft checks on draft.reply_text."""
    draft = state.get("draft") or {}
    result = review_draft(draft, facts=state.get("facts"))
    return {"reviewer": result}


def review_draft(
    draft: dict[str, Any],
    *,
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Return ``{status, failures, warnings, flags}``.

    ``status`` is ``pass`` or ``fail``. The graph decides blocked vs rework from
    ``rework_count``; this function does not set ``blocked``.
    """
    text = str(draft.get("reply_text") or "")
    citation = draft.get("citation_url")
    last_checked = draft.get("last_checked")
    row = draft.get("row")

    failures: list[str] = []
    warnings: list[str] = []

    failures.extend(_check_pii(text))
    failures.extend(_check_length(text))
    failures.extend(_check_url_scope(text, citation if isinstance(citation, str) else None))
    failures.extend(_check_scope(text, facts=facts))
    failures.extend(
        _check_uncited(
            text,
            citation=citation if isinstance(citation, str) else None,
            row=row if isinstance(row, int) else None,
            facts=facts,
        )
    )

    if _needs_last_checked(row if isinstance(row, int) else None) and not last_checked:
        warnings.append("MISSING_LAST_CHECKED")

    # Deduplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for code in HARD_FAILS:
        if code in failures and code not in seen:
            seen.add(code)
            ordered.append(code)
    for code in failures:
        if code not in seen:
            seen.add(code)
            ordered.append(code)

    status = "pass" if not ordered else "fail"
    flags = list(ordered) + list(warnings)
    result = {
        "status": status,
        "failures": ordered,
        "warnings": warnings,
        "flags": flags,
    }
    logger.info("reviewer status=%s failures=%s", status, ordered)
    return result


def _check_pii(text: str) -> list[str]:
    return ["PII"] if scan(text) else []


def _check_length(text: str) -> list[str]:
    return ["LENGTH"] if count_sentences(text) > 3 else []


def _check_url_scope(text: str, citation: str | None) -> list[str]:
    urls = _URL_RE.findall(text)
    if citation:
        urls.append(citation)
    for url in urls:
        cleaned = url.rstrip(".,;:!?")
        if not _url_allowed(cleaned):
            return ["URL_SCOPE"]
    return []


def _url_allowed(url: str) -> bool:
    if url in EDU_URLS or url in CITATION_URLS:
        return True
    if is_public_url(url):
        return True
    host = urlparse(url).hostname or ""
    host = host.lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in PUBLIC_DOMAINS)


def _check_scope(text: str, *, facts: dict[str, Any] | None) -> list[str]:
    corpus_texts = _corpus_texts()
    explainer_texts = _explainer_texts_from_facts(facts)
    exempt = mark_corpus_spans(text, corpus_texts, explainer_texts)
    exempt += mark_spans(text, all_template_sentences(), "template")
    # Also exempt verbatim fact answer sentences carried on the draft path.
    if facts:
        exempt += mark_spans(text, list(facts.get("answer_sentences") or []), "corpus")

    found: list[str] = []
    if check_advice(text, exempt):
        found.append("ADVICE")
    if check_buy_sell(text, exempt):
        found.append("BUY_SELL")
    if check_performance(text, exempt):
        found.append("PERFORMANCE")
    return found


def _check_uncited(
    text: str,
    *,
    citation: str | None,
    row: int | None,
    facts: dict[str, Any] | None,
) -> list[str]:
    """
    Each sentence must be corpus/template; otherwise a digit/%/₹/fee keyword fails.

    Factual drafts (rows 3–4, and row 1 with an appended fact) need citation_url
    in CITATION_URLS or EDU_URLS.
    """
    corpus_texts = _corpus_texts()
    answer_sentences = list((facts or {}).get("answer_sentences") or [])
    templates = all_template_sentences()

    sentences = _sentences(text)
    for sentence in sentences:
        if _is_allowed_sentence(sentence, corpus_texts, answer_sentences, templates):
            continue
        if _looks_factual(sentence):
            return ["UNCITED_CLAIM"]

    factual_row = row in {3, 4} or (
        row == 1 and bool(answer_sentences) and any(_looks_factual(s) for s in sentences[1:])
    )
    if factual_row:
        if not citation:
            return ["UNCITED_CLAIM"]
        allowed_urls = set(CITATION_URLS) | set(EDU_URLS)
        fact_url = (facts or {}).get("citation_url")
        if isinstance(fact_url, str):
            allowed_urls.add(fact_url)
        if citation not in allowed_urls and not is_public_url(citation):
            return ["UNCITED_CLAIM"]
    return []


_FAMILY_LEAD_RE = re.compile(
    r"^For (?:the |HDFC's )(?:equity|fof) schemes(?: in this list)? "
    r"\((?P<names>[^()]+)\):\s*(?P<rest>.*)$"
)
_NOT_COVERED_LEAD = "This isn't covered by the scheme information we hold."


@lru_cache(maxsize=1)
def _template_sentences() -> frozenset[str]:
    """Every sentence of every fixed template, for exact matching only."""
    out: set[str] = set()
    for tmpl in all_template_sentences():
        out.update(_sentences(tmpl))
    return frozenset(out)


def _is_allowed_sentence(
    sentence: str,
    corpus_texts: tuple[str, ...],
    answer_sentences: list[str],
    templates: frozenset[str],
) -> bool:
    """
    True when the sentence is grounded: an exact template sentence, row 5's fixed
    sentences, or verbatim corpus text (optionally behind a fixed lead-in or family lead).

    Lead-ins and family leads are prefixes, never a pass on their own: the text after
    them must be grounded too. Otherwise "Per the scheme page: <anything>" would pass.
    """
    _ = templates  # kept for the call signature; exact sentences come from the cache
    stripped = sentence.strip()
    if not stripped:
        return True
    if stripped in _template_sentences() or stripped == _NOT_COVERED_LEAD:
        return True
    # Row 5's second sentence names what was searched and the snapshot dates (digits),
    # so it needs an exact structural match rather than the digit heuristic.
    if is_not_covered_search_sentence(stripped):
        return True
    for lead in FACT_LEAD_INS:
        if stripped.startswith(lead):
            rest = stripped[len(lead) :].strip()
            return not rest or _is_allowed_sentence(rest, corpus_texts, answer_sentences, templates)
    family = _FAMILY_LEAD_RE.match(stripped)
    if family is not None:
        names = [n.strip() for n in family.group("names").split(",")]
        if not names or not set(names) <= set(SCHEME_DISPLAY.values()):
            return False
        rest = family.group("rest").strip()
        return not rest or _is_allowed_sentence(rest, corpus_texts, answer_sentences, templates)
    # Verbatim corpus / answer text: the sentence must be contained in it, not merely
    # contain it (extra words around a quoted fact are not grounded).
    norm = _norm(stripped)
    if not norm:
        return False
    for ans in answer_sentences:
        if ans and norm in _norm(ans):
            return True
    bare = stripped.rstrip(".")
    for block in corpus_texts:
        if norm in _norm(block):
            return True
        # Sentence may be a prefix of a longer corpus sentence (no trailing period).
        if bare and bare in block:
            return True
    return False


def _looks_factual(sentence: str) -> bool:
    return bool(_FACTUAL_MARKER.search(sentence) or _FEE_KEYWORD.search(sentence))


def _needs_last_checked(row: int | None) -> bool:
    return row in {3, 4} or row == 1  # factual drafts; soft warn


def _sentences(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    cleaned = re.sub(r"\s+", " ", text.strip())
    return [p.strip() for p in _SENTENCE_SPLIT.split(cleaned) if p.strip()]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).casefold()


@lru_cache(maxsize=1)
def _corpus_texts() -> tuple[str, ...]:
    corpus = load_corpus()
    return tuple(block.text for block in corpus.blocks.values())


def _explainer_texts_from_facts(facts: dict[str, Any] | None) -> list[str]:
    _ = facts
    # Canonical bullets land in P7; until then there are none.
    return []


# Re-export for tests.
__all__ = ["HARD_FAILS", "review_draft", "reviewer_node", "TEMPLATE_SENTENCES"]
