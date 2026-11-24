"""Fee explainer writer: ≤6 verbatim corpus bullets, 2 URLs (arch §7.7)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings
from app.corpus.loader import Corpus, load_corpus
from app.corpus.sentences import split_block_sentences
from app.guardrails.length import count_bullets
from app.weekly.cluster import Theme, review_text
from app.weekly.ingest import Review

_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    # Bare "exit" matches trading language ("exit the trade"); require the fee phrase.
    "exit_load": ("exit load",),
    "stamp_duty": ("stamp duty",),
    "expense_ratio": ("expense ratio", "expense"),
    "tax": ("tax", "taxed", "gst", "stt"),
}


@dataclass(frozen=True, slots=True)
class ExplainerBullet:
    text: str
    source_url: str
    scheme_id: str
    section: str
    last_checked: str
    snapshot: str
    topic: str


@dataclass(slots=True)
class ExplainerDocument:
    header: str
    bullets: list[dict[str, Any]]
    source_urls: list[str]
    last_checked: str
    body_md: str


def build_explainer(
    *,
    fee_theme: Theme,
    reviews_by_id: dict[str, Review],
    corpus: Corpus | None = None,
    settings: Settings | None = None,
) -> ExplainerDocument:
    cfg = settings or get_settings()
    facts = corpus or load_corpus()
    schemes = list(cfg.explainer_schemes)
    if len(schemes) != 2:
        raise ValueError("EXPLAINER_SCHEMES must list exactly 2 scheme ids")

    plan = _bullet_plan(schemes)
    topic_order = _order_by_fee_keywords(fee_theme, reviews_by_id)
    plan_by_topic = {item[0]: item for item in plan}

    selected: list[ExplainerBullet] = []
    for topic_key in topic_order:
        item = plan_by_topic.get(topic_key)
        if item is None:
            continue
        bullet = _extract_bullet(
            facts,
            topic=item[0],
            scheme_id=item[1],
            section=item[2],
            match_terms=item[3],
        )
        if bullet is not None and not any(b.text == bullet.text for b in selected):
            selected.append(bullet)
        if len(selected) >= 6:
            break

    for item in plan:
        if len(selected) >= 6:
            break
        if any(b.topic == item[0] for b in selected):
            continue
        bullet = _extract_bullet(
            facts,
            topic=item[0],
            scheme_id=item[1],
            section=item[2],
            match_terms=item[3],
        )
        if bullet is not None and not any(b.text == bullet.text for b in selected):
            selected.append(bullet)

    selected = selected[:6]
    if not selected:
        raise ValueError("fee explainer produced zero bullets")

    urls = _two_urls(facts, schemes, selected)
    allowed = facts.citation_urls
    for url in urls:
        if url not in allowed:
            raise ValueError(f"URL not in corpus citation set: {url}")

    scheme_names = [facts.scheme_names.get(s, s) for s in schemes]
    header = (
        f"Fee explainer — {scheme_names[0]} and {scheme_names[1]}. "
        "Scheme-level charges from the official scheme pages. "
        "Brokerage and trading charges are not covered here."
    )
    last_checked_values = sorted({b.last_checked for b in selected})
    if len(last_checked_values) == 1:
        last_checked = last_checked_values[0]
        footer_checked = last_checked
    else:
        last_checked = last_checked_values[0]
        footer_checked = "; ".join(f"{b.scheme_id} {b.last_checked}" for b in selected)

    body = _render(header, selected, urls, footer_checked)
    bullet_dicts = [
        {
            "text": b.text,
            "source_url": b.source_url,
            "scheme_id": b.scheme_id,
            "section": b.section,
            "last_checked": b.last_checked,
            "snapshot": b.snapshot,
        }
        for b in selected
    ]
    if count_bullets([b["text"] for b in bullet_dicts]) > 6:
        raise AssertionError("explainer exceeds 6 bullets")
    for bullet in selected:
        block = facts.get(bullet.scheme_id, bullet.section)
        if block is None or bullet.text not in block.text:
            raise AssertionError(
                f"bullet is not a substring of its block: {bullet.scheme_id}/{bullet.section}"
            )

    return ExplainerDocument(
        header=header,
        bullets=bullet_dicts,
        source_urls=urls,
        last_checked=last_checked,
        body_md=body,
    )


def _bullet_plan(
    schemes: list[str],
) -> list[tuple[str, str, str, tuple[str, ...]]]:
    equity, fof = schemes[0], schemes[1]
    return [
        ("exit_load_equity", equity, "Exit load", ("exit load",)),
        ("exit_load_fof", fof, "Exit load", ("exit load",)),
        ("stamp_duty", equity, "Stamp duty and tax", ("stamp duty",)),
        ("expense_equity", equity, "Key fund facts", ("expense ratio",)),
        ("expense_fof", fof, "Key fund facts", ("expense ratio",)),
        ("tax", equity, "Stamp duty and tax", ("taxed at 20%",)),
    ]


def _has_keyword(text_l: str, keyword: str) -> bool:
    """Word-boundary match so ``tax`` does not hit inside ``exact``."""
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text_l) is not None


def _order_by_fee_keywords(
    fee_theme: Theme,
    reviews_by_id: dict[str, Review],
) -> list[str]:
    counter: Counter[str] = Counter()
    for rid in fee_theme.review_ids:
        review = reviews_by_id.get(rid)
        if review is None:
            continue
        text_l = review_text(review).lower()
        for topic, keywords in _TOPIC_KEYWORDS.items():
            if any(_has_keyword(text_l, kw) for kw in keywords):
                counter[topic] += 1

    family_to_keys = {
        "exit_load": ["exit_load_equity", "exit_load_fof"],
        "stamp_duty": ["stamp_duty"],
        "expense_ratio": ["expense_equity", "expense_fof"],
        "tax": ["tax"],
    }
    default = [
        "exit_load_equity",
        "exit_load_fof",
        "stamp_duty",
        "expense_equity",
        "expense_fof",
        "tax",
    ]
    if not counter:
        return default

    ordered: list[str] = []
    for family, _ in counter.most_common():
        for key in family_to_keys.get(family, ()):
            if key not in ordered:
                ordered.append(key)
    for key in default:
        if key not in ordered:
            ordered.append(key)
    return ordered


def _extract_bullet(
    corpus: Corpus,
    *,
    topic: str,
    scheme_id: str,
    section: str,
    match_terms: tuple[str, ...],
) -> ExplainerBullet | None:
    block = corpus.get(scheme_id, section)
    if block is None:
        return None
    for sentence in split_block_sentences(block.text):
        lower = sentence.lower()
        if any(term.lower() in lower for term in match_terms) and sentence in block.text:
            return ExplainerBullet(
                text=sentence,
                source_url=block.source_url,
                scheme_id=scheme_id,
                section=section,
                last_checked=block.last_checked,
                snapshot=block.snapshot,
                topic=topic,
            )
    return None


def _two_urls(
    corpus: Corpus,
    schemes: list[str],
    bullets: list[ExplainerBullet],
) -> list[str]:
    urls: list[str] = []
    for bullet in bullets:
        if bullet.source_url not in urls:
            urls.append(bullet.source_url)
        if len(urls) == 2:
            return urls
    for sid in schemes:
        block = corpus.get(sid, "Exit load") or corpus.get(sid, "Key fund facts")
        if block and block.source_url not in urls:
            urls.append(block.source_url)
        if len(urls) == 2:
            return urls
    raise ValueError(f"expected exactly 2 source URLs, got {urls}")


def _render(
    header: str,
    bullets: list[ExplainerBullet],
    urls: list[str],
    last_checked: str,
) -> str:
    lines = [header, ""]
    for bullet in bullets:
        lines.append(
            f"- {bullet.text} (snapshot {bullet.snapshot}, last checked {bullet.last_checked})"
        )
    lines.append("")
    lines.append(f"Sources: {urls[0]}, {urls[1]} · Last checked: {last_checked}")
    return "\n".join(lines).strip() + "\n"
