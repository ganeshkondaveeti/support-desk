"""ISO-week trend labels: rising / recurring / one_off (arch §7.4, G12)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.weekly.cluster import Theme
from app.weekly.ingest import Review

IST = ZoneInfo("Asia/Kolkata")


def apply_trends(
    themes: list[Theme],
    reviews: list[Review],
    assignment: dict[str, str] | None = None,
    *,
    settings: Settings | None = None,
) -> list[Theme]:
    """Mutate each theme with ``weekly_counts`` and ``trend``."""
    _ = assignment  # assignment is available for callers; trends use theme.review_ids
    cfg = settings or get_settings()
    by_id = {r.id: r for r in reviews}
    for theme in themes:
        member_dates = [
            by_id[rid].date.astimezone(IST).date() for rid in theme.review_ids if rid in by_id
        ]
        weekly = bucket_by_iso_week(member_dates)
        theme.weekly_counts = {
            "raw": {k: v["count"] for k, v in weekly.items()},
            "rates": {k: round(v["rate"], 4) for k, v in weekly.items()},
            "days": {k: v["days"] for k, v in weekly.items()},
        }
        theme.trend = classify_trend(
            weekly,
            theme.count,
            rising_ratio=cfg.trend_rising_ratio,
            rising_min_sum=cfg.trend_rising_min_sum,
            recurring_week_ratio=cfg.trend_recurring_week_ratio,
            recurring_min_count=cfg.trend_recurring_min_count,
            min_week_days=cfg.trend_min_week_days,
        )
    return themes


def bucket_by_iso_week(dates: Iterable[date]) -> dict[str, dict[str, float | int]]:
    """
    Group dates into ISO weeks.

    Each entry: ``{count, days, rate}`` where ``rate = count / days_with_data``.
    """
    per_week_days: dict[str, set[date]] = defaultdict(set)
    per_week_count: dict[str, int] = defaultdict(int)
    for d in dates:
        key = _iso_week_key(d)
        per_week_days[key].add(d)
        per_week_count[key] += 1
    out: dict[str, dict[str, float | int]] = {}
    for key in sorted(per_week_count):
        days = len(per_week_days[key])
        count = per_week_count[key]
        out[key] = {
            "count": count,
            "days": days,
            "rate": (count / days) if days else 0.0,
        }
    return out


def classify_trend(
    weekly: dict[str, dict[str, float | int]],
    total_count: int,
    *,
    rising_ratio: float,
    rising_min_sum: int,
    recurring_week_ratio: float,
    recurring_min_count: int,
    min_week_days: int,
) -> str:
    """
    Evaluate rising → recurring → one_off.

    Edge weeks with fewer than ``min_week_days`` active days are excluded from
    rising math (G12). Rising also requires ≥ 2 full prior weeks.
    """
    ordered_keys = sorted(weekly)
    full_weeks = [k for k in ordered_keys if int(weekly[k]["days"]) >= min_week_days]

    if len(full_weeks) >= 3:
        last2 = full_weeks[-2:]
        prior = full_weeks[:-2]
        if len(prior) >= 2:
            last2_rates = [float(weekly[k]["rate"]) for k in last2]
            prior_rates = [float(weekly[k]["rate"]) for k in prior]
            last2_mean = sum(last2_rates) / len(last2_rates)
            prior_mean = sum(prior_rates) / len(prior_rates)
            last2_sum = sum(int(weekly[k]["count"]) for k in last2)
            if (
                prior_mean > 0
                and last2_mean >= rising_ratio * prior_mean
                and last2_sum >= rising_min_sum
            ):
                return "rising"

    # Recurring uses the full window (including edge weeks for "appears in").
    weeks_present = sum(1 for k in ordered_keys if int(weekly[k]["count"]) > 0)
    week_total = len(ordered_keys) or 1
    if weeks_present / week_total >= recurring_week_ratio and total_count >= recurring_min_count:
        return "recurring"
    return "one_off"


def _iso_week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_ending_as_of_values(window_start: date, window_end: date) -> list[date]:
    """Saturdays (ISO week ends) from the first full week through ``window_end``."""
    # Find the first Saturday on/after window_start.
    first = window_start
    while first.weekday() != 5:  # Saturday
        first += timedelta(days=1)
    out: list[date] = []
    cur = first
    while cur <= window_end:
        out.append(min(cur, window_end))
        cur += timedelta(days=7)
    if not out or out[-1] != window_end:
        out.append(window_end)
    # Unique, sorted
    return sorted(set(out))


def now_ist(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(tz=IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)
