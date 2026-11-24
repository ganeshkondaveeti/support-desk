"""Repeat-question bridge: fingerprint, 7-day counts, pulse section (arch §9.1)."""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from simhash import Simhash
from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import models
from app.db.repository import add
from app.guardrails.pii import TOKEN_BY_TYPE

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Architecture §9.1: 64-bit simhash, top 8 bits as the bucket.
SIMHASH_BITS = 64
BUCKET_BITS = 8

# Common English stopwords + MF filler that adds noise without meaning.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "when",
        "where",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "is",
        "am",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "can",
        "could",
        "should",
        "would",
        "may",
        "might",
        "must",
        "shall",
        "will",
        "i",
        "me",
        "my",
        "mine",
        "you",
        "your",
        "yours",
        "he",
        "she",
        "it",
        "we",
        "they",
        "them",
        "their",
        "this",
        "that",
        "these",
        "those",
        "of",
        "to",
        "for",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "as",
        "into",
        "about",
        "over",
        "after",
        "before",
        "between",
        "under",
        "again",
        "further",
        "once",
        "here",
        "there",
        "all",
        "any",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "also",
        "please",
        "kindly",
        "sir",
        "madam",
        "hello",
        "hi",
        "hey",
        "got",
        "get",
        "getting",
        "fund",
        "funds",
        "scheme",
        "schemes",
        "mutual",
        "app",
        "groww",
        "units",
        "unit",
        "rule",
        "rules",
        "explain",
        "clarify",
        "tell",
        "know",
        "want",
        "need",
        "help",
    }
)

# Map inflectional / synonym variants so exit-load rephrasings share a bucket.
# deduct≈charge and sell≈redeem collapse the "deducted when I sold" rephrasing
# onto the same simhash bucket as "charged on redemption" (P7.5).
_LEMMA: dict[str, str] = {
    "charges": "charge",
    "charged": "charge",
    "charging": "charge",
    "fees": "fee",
    "brokerages": "brokerage",
    "redeemed": "redeem",
    "redeeming": "redeem",
    "redemption": "redeem",
    "redemptions": "redeem",
    "deduct": "charge",
    "deducted": "charge",
    "deducting": "charge",
    "deduction": "charge",
    "deductions": "charge",
    "sold": "redeem",
    "selling": "redeem",
    "sell": "redeem",
    "sale": "redeem",
    "loads": "load",
    "loading": "load",
    "hit": "charge",
    "hdfcs": "hdfc",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.I)
_PII_TOKEN_RE = re.compile(
    r"\[(?:" + "|".join(re.escape(t.strip("[]")) for t in sorted(TOKEN_BY_TYPE.values())) + r")\]",
    re.I,
)
# Compact "exit-load" / "exit_load" → "exitload" so hyphenation doesn't split the pair.
_EXIT_LOAD_RE = re.compile(r"\bexit[\s\-_]?loads?\b", re.I)

BURST_WINDOW = timedelta(minutes=10)
BURST_MIN = 3


def normalize(text: str) -> str:
    """
    lowercase → strip PII tokens → strip stopwords/punctuation → lemmatize →
    sort unique tokens (arch §9.1).
    """
    if not text:
        return ""
    lowered = text.casefold()
    lowered = _PII_TOKEN_RE.sub(" ", lowered)
    lowered = _EXIT_LOAD_RE.sub(" exitload ", lowered)
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(lowered):
        tok = raw.casefold()
        tok = _LEMMA.get(tok, tok)
        if len(tok) < 2:
            continue
        if tok in _STOPWORDS:
            continue
        tokens.add(tok)
    return " ".join(sorted(tokens))


def simhash_bucket(normalized_text: str) -> str:
    """64-bit simhash → top 8 bits as a zero-padded hex bucket."""
    features = normalized_text.split() if normalized_text else ["_empty_"]
    value = int(Simhash(features, f=SIMHASH_BITS).value)
    bucket = value >> (SIMHASH_BITS - BUCKET_BITS)
    return f"{bucket:02x}"


def compliance_class(*, advice: bool = False, performance_claim: bool = False) -> str:
    """advice | performance | factual (arch §9.1)."""
    if advice:
        return "advice"
    if performance_claim:
        return "performance"
    return "factual"


def fingerprint(
    theme_id: str,
    section: str | None,
    compliance: str,
    text: str,
) -> str:
    """
    sha1(theme|section|compliance|simhash_bucket(normalized_text)).

    Scheme is deliberately omitted so Mid Cap / Small Cap exit-load questions collide.
    """
    section_key = section or "uncovered"
    bucket = simhash_bucket(normalize(text))
    material = f"{theme_id}|{section_key}|{compliance}|{bucket}"
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def window_bounds(
    now: datetime | None = None,
    *,
    days: int | None = None,
) -> tuple[datetime, datetime]:
    """
    Half-open window ``(now − days, now]`` in UTC; ``days`` from config (default 7).

    ``received_at`` is stored UTC; display uses IST (G1 / EC-BR-08).
    """
    clock = _as_utc(now or datetime.now(tz=UTC))
    span = days if days is not None else get_settings().repeat_window_days
    start = clock - timedelta(days=span)
    return start, clock


def repeat_count(
    session: Session,
    fp: str,
    now: datetime | None = None,
) -> int:
    """On-demand count of tickets with this fingerprint in the ticket-clock window."""
    start, end = _sqlite_window(now)
    stmt: Select[tuple[int]] = (
        select(func.count())
        .select_from(models.Ticket)
        .where(
            and_(
                models.Ticket.fingerprint == fp,
                models.Ticket.received_at > start,
                models.Ticket.received_at <= end,
            )
        )
    )
    return int(session.scalar(stmt) or 0)


def in_week_repeats(
    text_or_fingerprint: str,
    now: datetime | None = None,
    *,
    session: Session | None = None,
    theme_id: str | None = None,
    section: str | None = None,
    compliance: str = "factual",
) -> int:
    """
    Pattern-agent entry point.

    - 40-char hex → count by fingerprint.
    - Otherwise compute fingerprint from theme/section/compliance + text.
    - Without a session, returns 0 (tests that run agents in isolation).
    """
    if session is None:
        return 0
    if _looks_like_fingerprint(text_or_fingerprint):
        return repeat_count(session, text_or_fingerprint, now)
    theme = theme_id or "other"
    fp = fingerprint(theme, section, compliance, text_or_fingerprint)
    return repeat_count(session, fp, now)


def record_ticket_fingerprint(
    session: Session,
    ticket: models.Ticket,
    *,
    theme_id: str,
    section: str | None,
    compliance: str,
    question: str | None = None,
    now: datetime | None = None,
) -> str:
    """
    Compute fingerprint, store on the ticket, upsert ``question_clusters``.

    Returns the fingerprint. Count is always computed on demand — never cached.
    """
    _ = now  # reserved for callers that pin the ticket clock; seen_at uses received_at
    text = question if question is not None else ticket.question_redacted
    fp = fingerprint(theme_id, section, compliance, text)
    ticket.fingerprint = fp
    _upsert_cluster(
        session,
        fp=fp,
        representative_candidate=ticket.question_redacted,
        section=section,
        scheme_id=None,
        theme_id=theme_id,
        seen_at=_as_utc(ticket.received_at),
    )
    session.flush()
    logger.info(
        "fingerprint ticket=%s fp=%s theme=%s section=%s compliance=%s",
        ticket.id,
        fp[:12],
        theme_id,
        section or "uncovered",
        compliance,
    )
    return fp


def repeat_questions(
    now: datetime | None = None,
    *,
    min_count: int | None = None,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    """
    Clusters with ≥ min_count tickets in the ticket-clock window.

    Each item: representative_text, count, window_start, window_end, theme_id,
    possible_duplicate_burst, fingerprint, provenance=question (G3).
    """
    threshold = min_count if min_count is not None else get_settings().repeat_min
    start_aware, end_aware = window_bounds(now)
    start, end = _sqlite_window(now)

    def _run(sess: Session) -> list[dict[str, Any]]:
        stmt = (
            select(
                models.Ticket.fingerprint,
                func.count().label("cnt"),
            )
            .where(
                and_(
                    models.Ticket.fingerprint.is_not(None),
                    models.Ticket.received_at > start,
                    models.Ticket.received_at <= end,
                )
            )
            .group_by(models.Ticket.fingerprint)
            .having(func.count() >= threshold)
        )
        rows = list(sess.execute(stmt).all())
        out: list[dict[str, Any]] = []
        for fp, count in rows:
            if not fp:
                continue
            cluster = sess.get(models.QuestionCluster, fp)
            tickets = list(
                sess.scalars(
                    select(models.Ticket).where(
                        and_(
                            models.Ticket.fingerprint == fp,
                            models.Ticket.received_at > start,
                            models.Ticket.received_at <= end,
                        )
                    )
                ).all()
            )
            representative = _representative_text(tickets, cluster)
            burst = _burst_flag(tickets)
            window_start_ist = start_aware.astimezone(IST).date().isoformat()
            window_end_ist = end_aware.astimezone(IST).date().isoformat()
            out.append(
                {
                    "representative_text": representative,
                    "count": int(count),
                    "window_start": window_start_ist,
                    "window_end": window_end_ist,
                    "window": f"{window_start_ist} → {window_end_ist}",
                    "theme_id": (cluster.theme_id if cluster else None) or "other",
                    "possible_duplicate_burst": burst,
                    "fingerprint": fp,
                    "provenance": "question",
                }
            )
        out.sort(key=lambda item: (-int(item["count"]), str(item["representative_text"])))
        return out

    if session is not None:
        return _run(session)

    from app.db.session import session_scope

    with session_scope() as sess:
        return _run(sess)


def _upsert_cluster(
    session: Session,
    *,
    fp: str,
    representative_candidate: str,
    section: str | None,
    scheme_id: str | None,
    theme_id: str,
    seen_at: datetime,
) -> models.QuestionCluster:
    seen_at = _as_utc(seen_at)
    existing = session.get(models.QuestionCluster, fp)
    if existing is None:
        row = models.QuestionCluster(
            fingerprint=fp,
            representative_text=representative_candidate,
            section=section,
            scheme_id=scheme_id,
            theme_id=theme_id,
            first_seen=seen_at,
            last_seen=seen_at,
        )
        add(session, row)
        return row
    # SQLite may round-trip timezone-aware columns as naive UTC.
    existing_last = _as_utc(existing.last_seen)
    existing_first = _as_utc(existing.first_seen)
    existing.last_seen = max(existing_last, seen_at)
    if existing_first > seen_at:
        existing.first_seen = seen_at
    if theme_id and not existing.theme_id:
        existing.theme_id = theme_id
    if section and not existing.section:
        existing.section = section
    if scheme_id and not existing.scheme_id:
        existing.scheme_id = scheme_id
    return existing


def _representative_text(
    tickets: list[models.Ticket],
    cluster: models.QuestionCluster | None,
) -> str:
    if not tickets:
        return cluster.representative_text if cluster else ""
    counts = Counter(t.question_redacted for t in tickets)
    # Most frequent wording; tie-break by earliest received_at then lexical.
    best_text, _ = max(
        counts.items(),
        key=lambda item: (
            item[1],
            -min(
                (t.received_at.timestamp() for t in tickets if t.question_redacted == item[0]),
                default=0.0,
            ),
            item[0],
        ),
    )
    if cluster is not None and best_text != cluster.representative_text:
        cluster.representative_text = best_text
    return best_text


def _burst_flag(tickets: list[models.Ticket]) -> bool:
    """≥3 identical normalized texts within 10 minutes (EC-BR-03)."""
    by_norm: dict[str, list[datetime]] = {}
    for ticket in tickets:
        key = normalize(ticket.question_redacted)
        by_norm.setdefault(key, []).append(_as_utc(ticket.received_at))
    for times in by_norm.values():
        times.sort()
        for i in range(len(times)):
            window_end = times[i] + BURST_WINDOW
            n = sum(1 for t in times[i:] if t <= window_end)
            if n >= BURST_MIN:
                return True
    return False


def _looks_like_fingerprint(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{40}", value.strip().lower()))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        # SQLite returns naive UTC wall times for timezone=True columns.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sqlite_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Naive-UTC bounds for SQLite DateTime comparisons."""
    start, end = window_bounds(now)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)
