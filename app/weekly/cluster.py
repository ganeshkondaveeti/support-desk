"""Fixed 5-id theme clustering (arch §7.3)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.guardrails.allowlists import FIXED_THEME_IDS, register_themes
from app.weekly.ingest import Review

BASELINE_PATH = Path(__file__).resolve().parents[2] / "data" / "analyzed_themes.json"

# Tie-break priority: fees first so "charged brokerage on a failed order" is fee confusion.
THEME_PRIORITY: tuple[str, ...] = (
    "fees_brokerage",
    "money_movement",
    "reliability",
    "trading_charts",
    "other",
)

THEME_META: dict[str, dict[str, str]] = {
    "trading_charts": {
        "name": "Trading, charts & orders",
        "description": "User feedback related to trading, charts & orders.",
    },
    "money_movement": {
        "name": "Withdrawals, deposits & payments",
        "description": "User feedback related to withdrawals, deposits & payments.",
    },
    "reliability": {
        "name": "App stability & performance",
        "description": "User feedback related to app stability & performance.",
    },
    "fees_brokerage": {
        "name": "Fees & brokerage",
        "description": "User feedback related to fees & brokerage.",
    },
    "other": {
        "name": "Other feedback",
        "description": "Miscellaneous reviews that do not fit the primary themes.",
    },
}

# Indicative lexicon from arch §7.3, expanded for phrase hits.
LEXICON: dict[str, tuple[str, ...]] = {
    "fees_brokerage": (
        "exit load",
        "brokerage",
        "hidden charge",
        "hidden charges",
        "dp charge",
        "stamp duty",
        "amc charge",
        "hidden",
        "charges",
        "charged",
        "charge",
        "fees",
        "fee",
        "deducted",
        "commission",
        "gst",
    ),
    "money_movement": (
        "withdraw",
        "withdrawal",
        "deposit",
        "upi",
        "bank",
        "refund",
        "payment",
        "mandate",
        "settlement",
        "credited",
        "payout",
        "add money",
        "fund transfer",
        "transfer money",
    ),
    "reliability": (
        "crash",
        "slow",
        "lag",
        "hang",
        "not loading",
        "error",
        "bug",
        "logout",
        "otp not received",
        "freeze",
        "freezing",
        "stuck",
        "glitch",
        "not working",
        "doesn't work",
        "dont work",
        "force close",
        "keeps closing",
    ),
    "trading_charts": (
        "candlestick",
        "stop loss",
        "stoploss",
        "chart",
        "charts",
        "f&o",
        "fno",
        "option",
        "options",
        "intraday",
        "indicator",
        "candle",
        "gtt",
        "order",
        "orders",
        "trade",
        "trading",
        "square off",
        "margin",
        "position",
        "execution",
        "slippage",
    ),
}


@dataclass(slots=True)
class Theme:
    id: str
    name: str
    description: str
    review_ids: list[str]
    count: int
    avg_rating: float
    score: float
    window_start: date
    window_end: date
    trend: str = "one_off"
    weekly_counts: dict[str, Any] = field(default_factory=dict)
    is_top3: bool = False
    is_fee_theme: bool = False


@dataclass(slots=True)
class ClusterResult:
    themes: list[Theme]
    assignment: dict[str, str]
    used_baseline_fallback: bool
    lexicon_only_counts: dict[str, int]


def cluster_reviews(
    reviews: list[Review],
    *,
    window_start: date,
    window_end: date,
    use_baseline_fallback: bool = True,
) -> ClusterResult:
    """
    Assign each review to exactly one of the five fixed themes.

    When ``use_baseline_fallback`` is True (default), review ids present in
    ``analyzed_themes.json`` keep their Milestone 2 assignment so the ±2%
    regression holds (plan P3 risk R2). Lexicon scoring covers new ids only.
    """
    baseline = load_baseline_assignment() if use_baseline_fallback else {}
    assignment: dict[str, str] = {}
    lexicon_counts: dict[str, int] = {tid: 0 for tid in THEME_PRIORITY}
    used_baseline = False

    for review in reviews:
        if review.id in baseline:
            theme_id = baseline[review.id]
            used_baseline = True
        else:
            theme_id = assign_theme(review_text(review))
            lexicon_counts[theme_id] = lexicon_counts.get(theme_id, 0) + 1
        assignment[review.id] = theme_id

    by_theme: dict[str, list[Review]] = {tid: [] for tid in THEME_PRIORITY}
    for review in reviews:
        by_theme[assignment[review.id]].append(review)

    themes: list[Theme] = []
    for theme_id in THEME_PRIORITY:
        members = by_theme[theme_id]
        meta = THEME_META[theme_id]
        ratings = [r.rating for r in members]
        count = len(members)
        avg = round(sum(ratings) / count, 1) if count else 0.0
        score = theme_score(ratings)
        themes.append(
            Theme(
                id=theme_id,
                name=meta["name"],
                description=meta["description"],
                review_ids=[r.id for r in members],
                count=count,
                avg_rating=avg,
                score=score,
                window_start=window_start,
                window_end=window_end,
                is_fee_theme=(theme_id == "fees_brokerage"),
            )
        )

    register_themes(FIXED_THEME_IDS)
    return ClusterResult(
        themes=themes,
        assignment=assignment,
        used_baseline_fallback=used_baseline,
        lexicon_only_counts=lexicon_counts,
    )


def _lexicon_hits(text_l: str, terms: tuple[str, ...]) -> int:
    """Weighted term hits; multi-word phrases count double."""
    score = 0
    for term in terms:
        if term in text_l:
            score += 2 if " " in term else 1
    return score


def assign_theme(text: str) -> str:
    """
    Lexicon assignment with fees-first priority among matched themes.

    Any theme with at least one lexicon hit is a candidate; the fixed priority
    order breaks multi-theme reviews (EC-WK-07: brokerage + F&O → fees).
    Density scoring remains available via ``lexicon_hit_density`` for quotes.
    """
    text_l = text.lower()
    matched = {theme_id for theme_id, terms in LEXICON.items() if _lexicon_hits(text_l, terms) > 0}
    if not matched:
        return "other"
    for theme_id in THEME_PRIORITY:
        if theme_id in matched:
            return theme_id
    return "other"


def lexicon_hit_density(text: str, theme_id: str) -> int:
    terms = LEXICON.get(theme_id, ())
    return _lexicon_hits(text.lower(), terms)


def review_text(review: Review) -> str:
    return f"{review.title} {review.text}".strip()


def theme_score(ratings: list[int]) -> float:
    """Milestone 2 style: volume weighted by rating negativity (6 − rating)."""
    if not ratings:
        return 0.0
    return round(float(sum(6 - r for r in ratings)), 2)


def select_top3_and_fee(themes: list[Theme]) -> tuple[list[Theme], Theme]:
    """Top 3 by score excluding ``other``, plus forced ``fees_brokerage`` (arch §7.5)."""
    ranked = sorted(
        [t for t in themes if t.id != "other"],
        key=lambda t: (-t.score, -t.count, t.id),
    )
    top3 = ranked[:3]
    for theme in themes:
        theme.is_top3 = theme.id in {t.id for t in top3}
        theme.is_fee_theme = theme.id == "fees_brokerage"
    fee = next(t for t in themes if t.id == "fees_brokerage")
    return top3, fee


@lru_cache(maxsize=1)
def load_baseline_assignment() -> dict[str, str]:
    if not BASELINE_PATH.is_file():
        return {}
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for theme in payload.get("allThemes", []):
        theme_id = str(theme["id"])
        for review_id in theme.get("reviewIds", []):
            mapping[str(review_id)] = theme_id
    return mapping


def baseline_counts() -> dict[str, int]:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return {str(t["id"]): int(t["count"]) for t in payload.get("allThemes", [])}
