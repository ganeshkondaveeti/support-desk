"""Weekly loop orchestration: ingest → cluster → trends → pulse → explainer (arch §7)."""

from __future__ import annotations

import argparse
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bridge.repeats import repeat_questions
from app.config import get_settings
from app.corpus.loader import load_corpus
from app.db import models
from app.db.repository import add
from app.db.session import init_db, session_scope
from app.gate.actions import propose_weekly_actions
from app.ids import safe_hex_id
from app.weekly.cluster import (
    baseline_counts,
    cluster_reviews,
    select_top3_and_fee,
)
from app.weekly.explainer import build_explainer
from app.weekly.ingest import DEFAULT_REVIEWS_PATH, load_reviews
from app.weekly.pulse import ActionIdeasFn, build_pulse, default_llm_action_ideas
from app.weekly.trends import apply_trends, now_ist

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

Trigger = Literal["cron", "manual", "cli"]


@dataclass(slots=True)
class WeeklyRunResult:
    run_id: str
    as_of: date
    status: str
    noop: bool = False
    degraded: bool = False
    theme_counts: dict[str, int] = field(default_factory=dict)
    pulse_word_count: int = 0
    explainer_bullets: int = 0
    action_ids: list[str] = field(default_factory=list)
    used_baseline_fallback: bool = False
    error: str | None = None
    pulse_body: str = ""
    explainer_body: str = ""


class WeeklyRunLockError(RuntimeError):
    """Raised when another weekly run is already in progress."""

    def __init__(self, message: str, *, run_id: str | None = None) -> None:
        super().__init__(message)
        self.run_id = run_id


# Arch §11: DB-row lock with a 15-minute TTL (EC-WK-15).
LOCK_TTL_SECONDS = 15 * 60


def compute_input_hash(reviews_path: Path, as_of: date) -> str:
    raw = reviews_path.read_bytes()
    digest = hashlib.sha256()
    digest.update(raw)
    digest.update(b"|")
    digest.update(as_of.isoformat().encode("utf-8"))
    # Include corpus scheme set so a corpus swap invalidates the hash.
    try:
        corpus = load_corpus()
        digest.update(b"|")
        digest.update(",".join(sorted(corpus.scheme_ids)).encode("utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return digest.hexdigest()


def default_as_of(reviews_path: Path | None = None, *, now: datetime | None = None) -> date:
    ingested = load_reviews(reviews_path)
    today = now_ist(now).date()
    return min(today, ingested.window_end)


def run_weekly(
    as_of: date | None = None,
    *,
    trigger: Trigger = "manual",
    reviews_path: Path | str | None = None,
    db_url: str | None = None,
    now: datetime | None = None,
    action_ideas_fn: ActionIdeasFn | None = None,
    use_baseline_fallback: bool = True,
) -> WeeklyRunResult:
    """
    Run the weekly loop end to end.

    Idempotency (arch §7.1): for a **cron** trigger, a finished run with the same
    ``input_hash`` + ``as_of`` is a no-op. A manual or CLI trigger always creates a new
    run; its proposals supersede the previous run's *pending* actions only.
    Run lock: at most one ``status=running`` row at a time, for every trigger.
    """
    path = Path(reviews_path) if reviews_path else DEFAULT_REVIEWS_PATH
    init_db(db_url)
    now_dt = now_ist(now)
    effective_as_of = as_of or default_as_of(path, now=now_dt)
    input_hash = compute_input_hash(path, effective_as_of)

    with session_scope(db_url) as session:
        existing = (
            _find_finished(session, input_hash=input_hash, as_of=effective_as_of)
            if trigger == "cron"
            else None
        )
        if existing is not None:
            logger.info(
                "weekly noop run_id=%s as_of=%s hash=%s",
                existing.id,
                effective_as_of,
                input_hash[:12],
            )
            return WeeklyRunResult(
                run_id=existing.id,
                as_of=effective_as_of,
                status=existing.status,
                noop=True,
                degraded=_is_degraded(existing),
                error=existing.error,
            )

        running = _find_running(session)
        if running is not None:
            raise WeeklyRunLockError(
                "weekly run already in progress",
                run_id=running.id,
            )

        run_id = safe_hex_id("wr")
        run = models.WeeklyRun(
            id=run_id,
            as_of=effective_as_of,
            trigger=trigger if trigger != "cli" else "manual",
            status="running",
            started_at=datetime.now(UTC),
            finished_at=None,
            input_hash=input_hash,
            error=None,
        )
        add(session, run)
        session.flush()

    try:
        result = _execute_run(
            run_id=run_id,
            as_of=effective_as_of,
            path=path,
            db_url=db_url,
            now_dt=now_dt,
            action_ideas_fn=action_ideas_fn,
            use_baseline_fallback=use_baseline_fallback,
        )
        return result
    except Exception as exc:
        with session_scope(db_url) as session:
            run = session.get(models.WeeklyRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = str(exc)
                run.finished_at = datetime.now(UTC)
        logger.exception("weekly run failed run_id=%s", run_id)
        return WeeklyRunResult(
            run_id=run_id,
            as_of=effective_as_of,
            status="failed",
            error=str(exc),
        )


def _execute_run(
    *,
    run_id: str,
    as_of: date,
    path: Path,
    db_url: str | None,
    now_dt: datetime,
    action_ideas_fn: ActionIdeasFn | None,
    use_baseline_fallback: bool,
) -> WeeklyRunResult:
    ingested = load_reviews(path, as_of=as_of)
    clustered = cluster_reviews(
        ingested.reviews,
        window_start=ingested.window_start,
        window_end=ingested.window_end,
        use_baseline_fallback=use_baseline_fallback,
    )
    apply_trends(clustered.themes, ingested.reviews, clustered.assignment)
    top3, fee = select_top3_and_fee(clustered.themes)
    reviews_by_id = {r.id: r for r in ingested.reviews}

    ideas_fn = action_ideas_fn if action_ideas_fn is not None else default_llm_action_ideas
    corpus = load_corpus()
    explainer = build_explainer(
        fee_theme=fee,
        reviews_by_id=reviews_by_id,
        corpus=corpus,
    )

    pulse_id = safe_hex_id("p")
    explainer_id = safe_hex_id("fe")
    action_ids: list[str] = []

    with session_scope(db_url) as session:
        repeats = repeat_questions(
            now_dt,
            min_count=get_settings().repeat_min,
            session=session,
        )
        pulse = build_pulse(
            as_of=as_of,
            window_start=ingested.window_start,
            window_end=ingested.window_end,
            top3=top3,
            fee_theme=fee,
            reviews_by_id=reviews_by_id,
            now=now_dt,
            repeat_questions=repeats,
            action_ideas_fn=ideas_fn,
        )
        for theme in clustered.themes:
            add(
                session,
                models.ThemeSnapshot(
                    run_id=run_id,
                    theme_id=theme.id,
                    name=theme.name,
                    description=theme.description,
                    review_ids=theme.review_ids,
                    count=theme.count,
                    avg_rating=theme.avg_rating,
                    score=theme.score,
                    window_start=theme.window_start,
                    window_end=theme.window_end,
                    trend=theme.trend,
                    weekly_counts=theme.weekly_counts,
                    is_top3=theme.is_top3,
                    is_fee_theme=theme.is_fee_theme,
                ),
            )

        pulse_status = "degraded" if pulse.degraded else "ready"
        add(
            session,
            models.Pulse(
                id=pulse_id,
                run_id=run_id,
                body_md=pulse.body_md,
                word_count=pulse.word_count,
                quotes=pulse.quotes,
                action_ideas=pulse.action_ideas,
                repeat_questions=pulse.repeat_questions,
                status=pulse_status,
            ),
        )
        add(
            session,
            models.FeeExplainer(
                id=explainer_id,
                run_id=run_id,
                bullets=explainer.bullets,
                source_urls=explainer.source_urls,
                last_checked=explainer.last_checked,
                status="ready",
            ),
        )

        actions = propose_weekly_actions(
            session,
            pulse_id=pulse_id,
            pulse_body=pulse.body_md,
            explainer_id=explainer_id,
            explainer_body=explainer.body_md,
        )
        action_ids = [a.id for a in actions]

        run = session.get(models.WeeklyRun, run_id)
        assert run is not None
        run.status = "awaiting_approval"
        run.finished_at = datetime.now(UTC)
        notes: dict[str, Any] = {
            "dropped_invalid": ingested.dropped_invalid,
            "dropped_duplicate": ingested.dropped_duplicate,
            "used_baseline_fallback": clustered.used_baseline_fallback,
            "baseline_drift": _baseline_drift({t.id: t.count for t in clustered.themes}),
        }
        if pulse.degraded:
            notes["degraded"] = True
            notes["degraded_reason"] = "action_ideas_fallback"
            run.error = "degraded: action_ideas_fallback"
        else:
            # Persist ingest notes without marking failure.
            run.error = None
        # Stash structured notes on the audit log.
        add(
            session,
            models.AuditEvent(
                id=safe_hex_id("ae"),
                ts=datetime.now(UTC),
                actor="system",
                event="weekly_run_completed",
                entity_type="weekly_run",
                entity_id=run_id,
                details=notes,
            ),
        )

    logger.info(
        "weekly done run_id=%s as_of=%s themes=%s words=%s bullets=%s degraded=%s",
        run_id,
        as_of,
        {t.id: t.count for t in clustered.themes},
        pulse.word_count,
        len(explainer.bullets),
        pulse.degraded,
    )
    return WeeklyRunResult(
        run_id=run_id,
        as_of=as_of,
        status="awaiting_approval",
        degraded=pulse.degraded,
        theme_counts={t.id: t.count for t in clustered.themes},
        pulse_word_count=pulse.word_count,
        explainer_bullets=len(explainer.bullets),
        action_ids=action_ids,
        used_baseline_fallback=clustered.used_baseline_fallback,
        error="degraded: action_ideas_fallback" if pulse.degraded else None,
        pulse_body=pulse.body_md,
        explainer_body=explainer.body_md,
    )


def _find_finished(
    session: Session,
    *,
    input_hash: str,
    as_of: date,
) -> models.WeeklyRun | None:
    return session.scalars(
        select(models.WeeklyRun).where(
            models.WeeklyRun.input_hash == input_hash,
            models.WeeklyRun.as_of == as_of,
            models.WeeklyRun.status.in_(("awaiting_approval", "failed")),
        )
    ).first()


def _has_running(session: Session) -> bool:
    return _find_running(session) is not None


def _find_running(session: Session) -> models.WeeklyRun | None:
    """Return the active running row, clearing any that exceeded the lock TTL."""
    now = datetime.now(UTC)
    rows = list(
        session.scalars(select(models.WeeklyRun).where(models.WeeklyRun.status == "running")).all()
    )
    active: models.WeeklyRun | None = None
    for row in rows:
        started = row.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        age = (now - started.astimezone(UTC)).total_seconds()
        if age > LOCK_TTL_SECONDS:
            row.status = "failed"
            row.error = "stale lock TTL exceeded"
            row.finished_at = now
            logger.warning("weekly stale lock cleared run_id=%s age_s=%.0f", row.id, age)
            continue
        active = row
    if any(r.status == "failed" and r.error == "stale lock TTL exceeded" for r in rows):
        session.flush()
    return active


def _is_degraded(run: models.WeeklyRun) -> bool:
    return bool(run.error and "degraded" in run.error)


def _baseline_drift(counts: dict[str, int]) -> dict[str, Any]:
    try:
        expected = baseline_counts()
    except OSError:
        return {}
    drift: dict[str, Any] = {}
    for theme_id, exp in expected.items():
        got = counts.get(theme_id, 0)
        if exp == 0:
            continue
        pct = abs(got - exp) / exp
        drift[theme_id] = {"expected": exp, "got": got, "pct": round(pct, 4)}
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the weekly product pulse loop")
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="Data-time anchor (YYYY-MM-DD). Defaults to min(today IST, max review date).",
    )
    parser.add_argument(
        "--reviews",
        type=Path,
        default=None,
        help="Path to normalized_reviews.json or a CSV",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="SQLAlchemy database URL override",
    )
    args = parser.parse_args(argv)
    result = run_weekly(
        as_of=args.as_of,
        trigger="cli",
        reviews_path=args.reviews,
        db_url=args.db,
    )
    print(
        f"run_id={result.run_id} status={result.status} noop={result.noop} "
        f"as_of={result.as_of} themes={result.theme_counts} "
        f"words={result.pulse_word_count} bullets={result.explainer_bullets} "
        f"actions={len(result.action_ids)} degraded={result.degraded} "
        f"baseline_fallback={result.used_baseline_fallback}"
    )
    if result.pulse_body and not result.noop:
        print("--- pulse ---")
        print(result.pulse_body)
        print("--- explainer ---")
        print(result.explainer_body)
    if result.noop or result.status == "awaiting_approval":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
