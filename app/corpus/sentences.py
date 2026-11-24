"""Deterministic sentence selection from corpus blocks (architecture §8.4)."""

from __future__ import annotations

import re

from app.corpus.loader import Block
from app.guardrails.length import count_sentences

# Keyword → label prefixes for Key fund facts field picking.
_FIELD_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("expense ratio", "expense"), "Expense ratio"),
    (("fund size", "aum", "assets under"), "Fund size"),
    (("nav date",), "NAV date"),
    (("nav",), "NAV"),
    (("riskometer", "risk rated", "rated"), "Riskometer"),
    (("minimum sip", "min. for sip", "min sip", "sip"), "Min"),
    (("lumpsum", "lump sum"), "Minimum lumpsum"),
    (("category",), "Category"),
    (("plan",), "Plan"),
)


def split_block_sentences(text: str) -> list[str]:
    """Split a block into quoteable sentences (Key fund facts uses '. ' facts)."""
    if not text:
        return []
    # Prefer ". " splits for fact lists; fall back to length helper awareness.
    parts = [p.strip() for p in re.split(r"(?<=\.)\s+", text) if p.strip()]
    return parts


def select_sentences(block: Block, question: str, *, limit: int = 2) -> list[str]:
    """
    Pick ≤ `limit` sentences from `block` for a question.
    For Key fund facts, match field labels to question keywords.
    Other sections: first 1–2 sentences.
    """
    sentences = split_block_sentences(block.text)
    if not sentences:
        return []

    if block.section == "Key fund facts":
        picked = _pick_key_facts(sentences, question.lower())
        return picked[:limit]

    return sentences[:limit]


def _pick_key_facts(sentences: list[str], question_l: str) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    for idx, sentence in enumerate(sentences):
        label = sentence.split(":", 1)[0].strip().lower()
        rank = 100
        for order, (keywords, prefix) in enumerate(_FIELD_KEYWORDS):
            if any(kw in question_l for kw in keywords):
                if label.startswith(prefix.lower()) or any(kw in label for kw in keywords):
                    rank = order
                    break
        if rank < 100:
            scored.append((rank, idx, sentence))
    if scored:
        scored.sort()
        return [s for _, _, s in scored]
    return sentences


def block_sentence_count(block: Block) -> int:
    return count_sentences(block.text)
