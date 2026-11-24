"""Weekly loop tests (P3)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.corpus.loader import load_corpus
from app.db import models
from app.db.session import init_db, reset_engine, session_scope
from app.guardrails.length import count_words
from app.weekly.cluster import (
    assign_theme,
    baseline_counts,
    cluster_reviews,
    select_top3_and_fee,
)
from app.weekly.explainer import build_explainer
from app.weekly.ingest import Review, load_reviews
from app.weekly.pipeline import run_weekly
from app.weekly.pulse import build_pulse, resolve_action_ideas, select_quote
from app.weekly.trends import apply_trends, bucket_by_iso_week, classify_trend
from sqlalchemy import select

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parents[1]
REVIEWS = ROOT / "data" / "normalized_reviews.json"


@pytest.fixture()
def db_url(tmp_path):  # type: ignore[no-untyped-def]
    reset_engine()
    url = f"sqlite:///{tmp_path / 'weekly.sqlite3'}"
    init_db(url)
    yield url
    reset_engine()


@pytest.fixture(scope="module")
def ingested():
    return load_reviews(REVIEWS, as_of=date(2026, 7, 4))


def _ok_ideas(top3, fee_terms, repeats):  # type: ignore[no-untyped-def]
    return [
        "Clarify chart order status when the exchange feed is delayed.",
        "Show withdrawal ETA on the payments confirmation screen.",
        "Add a status banner when OTP delivery is degraded.",
    ]


def test_ingest_optional_version_and_window(ingested) -> None:  # type: ignore[no-untyped-def]
    assert len(ingested.reviews) == 1650
    assert ingested.window_start == date(2026, 4, 12)
    assert ingested.window_end == date(2026, 7, 4)
    assert ingested.dropped_invalid == 0
    # 227 reviews omit version entirely — must still load.
    missing_version = sum(1 for r in ingested.reviews if r.version is None)
    assert missing_version >= 200


def test_cluster_baseline_within_2pct(ingested) -> None:  # type: ignore[no-untyped-def]
    result = cluster_reviews(
        ingested.reviews,
        window_start=ingested.window_start,
        window_end=ingested.window_end,
        use_baseline_fallback=True,
    )
    assert len(result.themes) == 5
    expected = baseline_counts()
    for theme in result.themes:
        assert len(theme.review_ids) == theme.count
        exp = expected[theme.id]
        assert abs(theme.count - exp) <= exp * 0.02
    assert result.used_baseline_fallback is True


def test_EC_WK_07_fee_tie_break() -> None:
    assert assign_theme("brokerage on F&O orders failed") == "fees_brokerage"


def test_top3_excludes_other_and_forces_fee(ingested) -> None:  # type: ignore[no-untyped-def]
    clustered = cluster_reviews(
        ingested.reviews,
        window_start=ingested.window_start,
        window_end=ingested.window_end,
    )
    apply_trends(clustered.themes, ingested.reviews, clustered.assignment)
    top3, fee = select_top3_and_fee(clustered.themes)
    assert len(top3) == 3
    assert all(t.id != "other" for t in top3)
    assert [t.id for t in top3] == ["trading_charts", "money_movement", "reliability"]
    assert fee.id == "fees_brokerage"
    assert fee.is_fee_theme is True


def test_trend_excludes_1day_edge_week_from_rising() -> None:
    # One-day first week must not unlock rising (G12 / EC-PT-05).
    weekly = {
        "2026-W15": {"count": 13, "days": 1, "rate": 13.0},
        "2026-W16": {"count": 10, "days": 7, "rate": 10 / 7},
        "2026-W17": {"count": 10, "days": 7, "rate": 10 / 7},
        "2026-W18": {"count": 40, "days": 7, "rate": 40 / 7},
        "2026-W19": {"count": 40, "days": 7, "rate": 40 / 7},
    }
    # After dropping the 1-day edge week, prior full weeks are only W16/W17 (2),
    # last2 = W18/W19 which rise — this SHOULD be rising.
    label = classify_trend(
        weekly,
        total_count=113,
        rising_ratio=1.5,
        rising_min_sum=5,
        recurring_week_ratio=0.5,
        recurring_min_count=20,
        min_week_days=4,
    )
    assert label == "rising"

    # With only a 1-day first week and fewer than 2 full prior weeks → not rising.
    short = {
        "2026-W15": {"count": 13, "days": 1, "rate": 13.0},
        "2026-W16": {"count": 5, "days": 7, "rate": 5 / 7},
        "2026-W17": {"count": 40, "days": 7, "rate": 40 / 7},
    }
    label2 = classify_trend(
        short,
        total_count=58,
        rising_ratio=1.5,
        rising_min_sum=5,
        recurring_week_ratio=0.5,
        recurring_min_count=20,
        min_week_days=4,
    )
    assert label2 != "rising"


def test_bucket_iso_weeks() -> None:
    dates = [date(2026, 4, 12), date(2026, 4, 13), date(2026, 4, 20)]
    buckets = bucket_by_iso_week(dates)
    assert buckets["2026-W15"]["count"] == 1  # Apr 12 Sunday
    assert buckets["2026-W15"]["days"] == 1


def test_pulse_word_cap_all_weekly_as_of(ingested) -> None:  # type: ignore[no-untyped-def]
    # ~12 week-ending Saturdays in the review window, plus final as_of.
    as_of_values: list[date] = []
    cur = date(2026, 4, 18)  # first Saturday on/after Apr 12
    while cur <= date(2026, 7, 4):
        as_of_values.append(cur)
        cur += timedelta(days=7)
    if as_of_values[-1] != date(2026, 7, 4):
        as_of_values.append(date(2026, 7, 4))
    assert len(as_of_values) >= 12

    for as_of in as_of_values:
        subset = load_reviews(REVIEWS, as_of=as_of)
        clustered = cluster_reviews(
            subset.reviews,
            window_start=subset.window_start,
            window_end=subset.window_end,
        )
        apply_trends(clustered.themes, subset.reviews, clustered.assignment)
        top3, fee = select_top3_and_fee(clustered.themes)
        pulse = build_pulse(
            as_of=as_of,
            window_start=subset.window_start,
            window_end=subset.window_end,
            top3=top3,
            fee_theme=fee,
            reviews_by_id={r.id: r for r in subset.reviews},
            now=datetime(2026, 11, 25, tzinfo=IST),
            action_ideas_fn=_ok_ideas,
        )
        assert pulse.word_count <= 250
        assert count_words(pulse.body_md) == pulse.word_count
        fee_line = next(ln for ln in pulse.body_md.splitlines() if "Most cited:" in ln)
        # Real top terms — not a fixed "exit load"-only label (G4).
        cited = fee_line.split("Most cited:", 1)[1].lower()
        assert "charges" in cited or "brokerage" in cited or "hidden" in cited or "fee" in cited


def test_explainer_caps_and_verbatim(ingested) -> None:  # type: ignore[no-untyped-def]
    corpus = load_corpus()
    clustered = cluster_reviews(
        ingested.reviews,
        window_start=ingested.window_start,
        window_end=ingested.window_end,
    )
    fee = next(t for t in clustered.themes if t.id == "fees_brokerage")
    doc = build_explainer(
        fee_theme=fee,
        reviews_by_id={r.id: r for r in ingested.reviews},
        corpus=corpus,
    )
    assert len(doc.bullets) <= 6
    assert len(set(doc.source_urls)) == 2
    assert all(url in corpus.citation_urls for url in doc.source_urls)
    assert "Brokerage and trading charges are not covered" in doc.header
    for bullet in doc.bullets:
        block = corpus.get(bullet["scheme_id"], bullet["section"])
        assert block is not None
        assert bullet["text"] in block.text


def test_action_ideas_llm_failure_marks_degraded(ingested) -> None:  # type: ignore[no-untyped-def]
    clustered = cluster_reviews(
        ingested.reviews,
        window_start=ingested.window_start,
        window_end=ingested.window_end,
    )
    top3, _fee = select_top3_and_fee(clustered.themes)

    def boom(*_a, **_k):  # type: ignore[no-untyped-def]
        raise RuntimeError("llm down")

    ideas, degraded = resolve_action_ideas(
        top3=top3,
        fee_terms=["charges"],
        repeats=[],
        action_ideas_fn=boom,
    )
    assert degraded is True
    assert len(ideas) == 3


def test_planted_pan_never_in_pulse() -> None:
    reviews = [
        Review(
            id="syn_pan",
            source="test",
            rating=1,
            title="",
            text="App charged me. Contact ABCDE1234F for refund help please now.",
            date=datetime(2026, 7, 1, tzinfo=IST),
            version=None,
            pii_types=("PAN",),
            text_had_pii=True,
        ),
        Review(
            id="syn_clean",
            source="test",
            rating=1,
            title="",
            text="Charts freeze every morning during market open which wastes time.",
            date=datetime(2026, 7, 1, tzinfo=IST),
            version=None,
        ),
    ]
    # Force both into trading_charts via synthetic theme construction.
    from app.weekly.cluster import Theme

    theme = Theme(
        id="trading_charts",
        name="Trading",
        description="t",
        review_ids=["syn_pan", "syn_clean"],
        count=2,
        avg_rating=1.0,
        score=10.0,
        window_start=date(2026, 7, 1),
        window_end=date(2026, 7, 1),
        trend="one_off",
        is_top3=True,
    )
    fee = Theme(
        id="fees_brokerage",
        name="Fees",
        description="f",
        review_ids=[],
        count=0,
        avg_rating=0.0,
        score=0.0,
        window_start=date(2026, 7, 1),
        window_end=date(2026, 7, 1),
        trend="one_off",
        is_fee_theme=True,
    )
    by_id = {r.id: r for r in reviews}
    quote = select_quote(theme, by_id)
    assert quote is not None
    assert quote.review_id == "syn_clean"
    pulse = build_pulse(
        as_of=date(2026, 7, 1),
        window_start=date(2026, 7, 1),
        window_end=date(2026, 7, 1),
        top3=[theme],
        fee_theme=fee,
        reviews_by_id=by_id,
        action_ideas_fn=_ok_ideas,
    )
    assert "ABCDE1234F" not in pulse.body_md
    assert "[PAN]" not in json.dumps(pulse.quotes)


def test_advisory_pct_buysell_never_quoted() -> None:
    from app.weekly.cluster import Theme

    reviews = [
        Review(
            id="adv",
            source="test",
            rating=1,
            title="",
            text="You should sell everything and switch to a better fund today.",
            date=datetime(2026, 7, 1, tzinfo=IST),
            version=None,
        ),
        Review(
            id="pct",
            source="test",
            rating=1,
            title="",
            text="The app showed 18% returns last year which looks wrong to me.",
            date=datetime(2026, 7, 1, tzinfo=IST),
            version=None,
        ),
        Review(
            id="buy",
            source="test",
            rating=1,
            title="",
            text="Please buy more of this stock for me when the chart dips again.",
            date=datetime(2026, 7, 1, tzinfo=IST),
            version=None,
        ),
        Review(
            id="ok",
            source="test",
            rating=1,
            title="",
            text="Order placement screen freezes whenever I open the chart panel.",
            date=datetime(2026, 7, 1, tzinfo=IST),
            version=None,
        ),
    ]
    theme = Theme(
        id="trading_charts",
        name="Trading",
        description="t",
        review_ids=[r.id for r in reviews],
        count=4,
        avg_rating=1.0,
        score=20.0,
        window_start=date(2026, 7, 1),
        window_end=date(2026, 7, 1),
        trend="one_off",
        is_top3=True,
    )
    quote = select_quote(theme, {r.id: r for r in reviews})
    assert quote is not None
    assert quote.review_id == "ok"


def test_pipeline_cp1_and_idempotent(db_url: str) -> None:
    result = run_weekly(
        as_of=date(2026, 7, 4),
        trigger="cli",
        db_url=db_url,
        action_ideas_fn=_ok_ideas,
        now=datetime(2026, 11, 25, 12, 0, tzinfo=IST),
    )
    assert result.noop is False
    assert result.status == "awaiting_approval"
    assert len(result.theme_counts) == 5
    assert result.pulse_word_count <= 250
    assert result.explainer_bullets <= 6
    assert len(result.action_ids) == 4
    assert result.degraded is False

    with session_scope(db_url) as session:
        run = session.get(models.WeeklyRun, result.run_id)
        assert run is not None
        assert run.status == "awaiting_approval"
        themes = session.scalars(
            select(models.ThemeSnapshot).where(models.ThemeSnapshot.run_id == result.run_id)
        ).all()
        assert len(themes) == 5
        pulse = session.scalars(
            select(models.Pulse).where(models.Pulse.run_id == result.run_id)
        ).one()
        assert pulse.word_count <= 250
        explainer = session.scalars(
            select(models.FeeExplainer).where(models.FeeExplainer.run_id == result.run_id)
        ).one()
        assert len(explainer.bullets) <= 6
        actions = session.scalars(select(models.ProposedAction)).all()
        assert len(actions) == 4
        assert all(a.status == "pending" for a in actions)

    # Cron with unchanged input is a no-op (arch §7.1).
    second = run_weekly(
        as_of=date(2026, 7, 4),
        trigger="cron",
        db_url=db_url,
        action_ideas_fn=_ok_ideas,
        now=datetime(2026, 11, 25, 12, 0, tzinfo=IST),
    )
    assert second.noop is True
    assert second.run_id == result.run_id

    # A manual re-run with the same input creates a new run and supersedes only the
    # previous run's pending actions.
    with session_scope(db_url) as session:
        first_actions = session.scalars(select(models.ProposedAction)).all()
        executed_id = first_actions[0].id
        first_actions[0].status = "executed"
    third = run_weekly(
        as_of=date(2026, 7, 4),
        trigger="manual",
        db_url=db_url,
        action_ideas_fn=_ok_ideas,
        now=datetime(2026, 11, 25, 12, 0, tzinfo=IST),
    )
    assert third.noop is False
    assert third.run_id != result.run_id
    with session_scope(db_url) as session:
        old = session.scalars(
            select(models.ProposedAction).where(models.ProposedAction.id != executed_id)
        ).all()
        by_status = {a.id: a.status for a in old}
        assert session.get(models.ProposedAction, executed_id).status == "executed"
        assert sum(1 for s in by_status.values() if s == "superseded") == 3
        assert sum(1 for s in by_status.values() if s == "pending") == 4
